# Quant Bot Pro: Full Research Pipeline Five-Gate Audit (2026-10-03)

**Reset point:** CRRP-2026-10-02 · **Code:** `ef5636b`, plus one uncommitted one-line Kalman-writer fix and the uncommitted retest module.

**Mode:** audit only.
- No module was rewritten, no strategy or threshold changed, no expensive experiment run, nothing traded, no holdout touched, no historical result deleted.
- Every diagnostic used synthetic data or a read-only scan, with the network refused.

**The evidence standard of this report:** a gate is PASS only where the question was actually verified (known-answer test, perturb-the-future check, calibration, or line-level code reading). Otherwise it is PARTIAL, FAIL or UNKNOWN.

---

## 1. Executive summary

**Can Quant Bot Pro currently produce canonical research evidence? No.** No research engine is canonical-capable today (§22). Several core *components* now pass their gates:
- the canonical Kalman filter;
- the Engle–Granger test with BH;
- the corrected pairs P&L;
- the regime detector;
- the Phase 3 backtester mechanics;
- the DCA simulator.

The *pipelines* built on them still fail at least one critical gate.

**Critical findings, each proven by a diagnostic in this audit:**

| ID | Finding | Proof |
|---|---|---|
| **F-01** | **PCA residual look-ahead** (`pca_engine.run_pca_engine`) | Changing only prices at t ≥ 2,000 changed residuals at t < 1,900 (max 0.0040) |
| **F-02** | **IID p-values on overlapping labels** (`oos_validator` per-fold Spearman; contagion `hypothesis_7` IC; feature-validator analytic `ic_pvalue`) | With H₀ true, 24-hour overlapping windows give a **67.7 %** false-positive rate at nominal 5 % |
| **F-03** | **Legacy Kalman seed look-ahead**: an OLS over the first 10,000 candles seeds every legacy pairs run, the research-lab pairs tool, and yesterday's partial retest | On a pair whose β changes from 0.8 to 1.6, the 10,000-candle OLS seed gives **β = −0.856**; the causal 1,000-candle seed gives 0.802 |

**High findings:**
- **F-04:** zero-latency same-close execution in the cross-sectional, contagion and feature-label research.
- **F-05:** pairs permutation null still legacy (B = 100).
- **F-06:** walk-forward boundary overlap with no purge.
- **F-07:** permutation floors vs multiple-testing families. B = 100 almost everywhere, and the FX survivor used about 20.
- **F-08:** the planned DCA significance design has **≈ 0 % power** (calibration).
- **F-10:** survivorship in hardcoded universes.
- **F-11:** research-lab verdicts labelled SUPPORTED without costs or OOS; a field called `fdr_adjusted_p_value` that is the raw p.
- **F-16:** repeated cointegration scans with no cross-scan family.

**Verified genuinely fixed (re-audited, not assumed):**
- C-01 pairs P&L identity (tests incl. the random-walk null pass).
- H-03 Engle–Granger: size 6.0 % on unrelated random walks, power 100 % on cointegrated pairs, BH 1/200 false vs 50/50 true.
- The canonical Kalman filter is causal (perturbing the future leaves the past byte-identical) and recovers a known β (1.501 vs 1.5).
- The regime detector and its precompute are causal.
- H-07 / M-01 / M-02 / H-11.
- DCA B1–B8.
- Journal mode separation.
- The evidence registry and dashboard badges.

**Corrections to my own earlier statements:**
1. The governance report and the retest said the canonical Kalman init window **L was owner-BLOCKED**. It is **locked at L = 1000** (REVB-3; `protocol.py` `init_window_candles = 1000`; D1–D5 report §10). The Decision Lock's C6 text is stale. What blocks canonical pairs evidence is **zone S**, not L.
2. The governance report described the price data as clean. This audit finds a partial last candle in 142 of 155 files (**F-09**).

**Research conclusion standard:** **no strategy has an established edge.** For every family, the correct description is "evidence invalidated", "evidence insufficient" or "edge not established" (§18). Nothing in this audit supports "no edge" for any family either.

---

## 2. Audit scope

**Read and probed:**
- **Research engines:** all of `bot/research/*`.
- **Canonical core:** `bot/quant_core/*`.
- **Pairs:** `bot/pairs/*` research-relevant parts.
- **Engines:** `bot/engines/regime_detector.py`, `simulation.py`, `strategy_scorer.py`.
- **Backtesting:** `bot/backtesting/*`.
- **Research lab:** `bot/research_lab/{verdict,tools}`.
- **Data:** the price data (all 446 files) and the journal.

**Depth:** line-level for the highest-risk paths (Kalman, cointegration, entry/exit P&L, permutation designs, OOS/walk-forward, PCA, cross-sectional timing, contagion alignment, feature validator); targeted scans for the rest. Coverage limits are listed in §25.

## 3. Repository research architecture

| Layer | Modules |
|---|---|
| Data | `data_fetcher.py` (REST), `fetch_all_symbols.py` → `data/*_1h.csv`; MT5/Yahoo for FX → `data/forex/` |
| Canonical core | `quant_core/{kalman, cointegration, seed, lifecycle, protocol, provenance, accounting, costs, metrics, calibration, time_grid, targets}` |
| Pairs research | `cointegration_engine` → `kalman_filter_engine` → `entry_exit_engine` (LEGACY / CANONICAL / CANONICAL_UNCERTIFIED) → `permutation_test_engine`, `walk_forward_engine`; `cointegration_pipeline_runner`; parity modules (`parity_replay`, `parity_forensics`, `parity_datasets`) |
| Feature research | `feature_calculator` → `build_observations` → `feature_validator` (+ `apply_family_wide_correction`), `feature_stability_analyzer`, `feature_decay_analyzer`, `validated_feature_registry` |
| Cross-asset | `cross_section_engine`, `run_cross_sectional_oos`, `cross_sectional_permutation_test`, `pca_engine`, `contagion_engine`, `run_contagion_feature_validation` |
| Regime | `engines/regime_detector`, `regime_labels`, `regime_conditional_{ic,oos,pairs,permutation,status}` |
| Execution research | `orderbook_depth_engine`, `order_book_depth_check`, `trade_flow_engine`, `derivatives_engine` |
| DCA | `dca_engine`, `dca_calibration` |
| OOS / statistics | `oos_validator`, `walk_forward_engine`, `permutation_stats`, `strategy_oos_adapters` |
| Backtesting | `backtesting/{backtester, portfolio_backtester, regime_precomputer}`, `engines/{simulation, strategy_scorer}` |
| Research lab | `research_lab/{orchestrator, verdict, tools/*, trial_service, data_fingerprint, evidence_interface}` |
| Governance | `research/evidence_registry.py` + `research/governance/*` (CRRP registry, data manifest) |

## 4. Five-gate definitions (as applied)

| Gate | PASS requires |
|---|---|
| G1 Software | Correct I/O, state and timestamps; edge cases handled; no silent fallbacks; tests that would fail on the defect |
| G2 Mathematics | Formulas, units, signs and indexing verified by known answers or invariants |
| G3 Statistics | An appropriate null, demonstrated size ≈ α and power, dependence handled, multiplicity controlled, honest OOS |
| G4 Quant / trading | Causal timing, next-bar execution, realistic costs/borrow/liquidity, liquidation-value risk, censoring |
| G5 Research integrity | Provenance (data, code, method, protocol, run ID), no look-ahead or selection leakage, reproducible, classified |

PASS / PARTIAL / FAIL / UNKNOWN / N/A. **One critical FAIL means the component is not validated.**

---

## 5. Cointegration audit (`cointegration_engine.py`, `quant_core/cointegration.py`)

| Gate | Status | Evidence |
|---|---|---|
| G1 | PASS | EG → `Result` (OK / UNTESTABLE, never a substitute p-value); NaN segments refused; BH over the scan family (`_apply_fdr_correction`); a raw pass failing FDR sets `passes_filters=False` |
| G2 | PASS | `ols_hedge` = OLS of x on [1, y]; MacKinnon p via statsmodels `coint(trend="c", autolag="aic")`; known-answer β recovery (Kalman probe K1) |
| G3 | PARTIAL | **Calibrated:** EG1 size 0.060 (n = 200 unrelated random-walk pairs), EG2 power 1.00 (50 cointegrated pairs, half-life about 49 h), EG3 BH false discoveries 1/200, true 50/50. **But (F-16):** repeated scans (100-coin, 19-symbol rescan, 3 appended pairs, FX scans) are separate BH families; there is no cross-scan control. Selection used the whole exposed history. Crypto assets share a market factor, which makes spurious cointegration more likely than in the independent-random-walk calibration. |
| G4 | PARTIAL | Cointegration ≠ tradeability: half-life filters exist, but costs and borrow are not part of selection |
| G5 | FAIL | `cointegration_pairs.csv` predates the EG fix (SUPERSEDED); zone S is BLOCKED, so no canonical selection exists; the "untouched holdout" ADF naming hazard (**F-19**) persists in code |

## 6. Kalman audit (`quant_core/kalman.py` canonical; `kalman_filter_engine.py`)

| Item | Finding |
|---|---|
| State / transition | θ = [β, α], random walk F = I, Q = diag(1e-5, 1e-5) |
| Observation | ln P_A = β ln P_B + α + v, R = 1e-3 |
| Update | Innovation e; S = HPHᵀ + R; K = PHᵀ/S; Joseph covariance; filtered residual e·R/S. Single shared implementation (`measurement_update`) |
| Gaps | Prediction-only steps; invalidation after g_max; canonical re-seed with segment reset of the z window |
| z-score | Trailing 504-candle window, min 168 valid, current included, ddof = 1, clip; causal |
| Canonical init | `causal_init`: OLS on the **L = 1000** candles **before** the seed (Design A) |
| **Legacy init (F-03)** | OLS on the **first 10,000** candles, applied from candle 0 |

| Gate | Canonical path | Legacy path |
|---|---|---|
| G1 | PASS (+ **F-18**: the legacy writer emitted the timestamp column as `index`; fixed uncommitted) | PASS |
| G2 | PASS: K1 recovers β = 1.501 / α = 0.19 against true 1.5 / 0.2 | PASS (same update) |
| G3 | N/A (a state estimator, not a test) | N/A |
| G4 | PASS: K2 shows perturbing t ≥ 4,000 leaves θ and z before 4,000 byte-identical | **FAIL**: K3 seed β = −0.856 vs true 0.8 (look-ahead into the first 10,000 candles) |
| G5 | PARTIAL: seed lineage and provenance implemented; Q/R/H frozen, never sensitivity-tested | FAIL |

**Q/R sensitivity:** Q and R are frozen baseline conventions with no sensitivity analysis (UNKNOWN robustness).

## 7. Contagion audit (`contagion_engine.py`, `run_contagion_feature_validation.py`)

| Gate | Status | Evidence |
|---|---|---|
| G1 | PASS | Causal rolling features; expanding bounds `shift(1)`; labels `shift(-N)`, named forward |
| G2 | PASS | Divergence = altcoin cumret − BTC cumret over the same trailing window; z on trailing stats |
| G3 | **FAIL** | Validation through the feature validator: B = 100 (floor 0.0099) against a 2,156-test BH family, so the negative is uninformative (**F-07**). `hypothesis_7_ic_report.csv` uses analytic IID p-values (p = 0.0 at n = 43,750) on overlapping labels (**F-02**: 67.7 % size inflation demonstrated) |
| G4 | PARTIAL | A label from T+1 means trading at the signal close with zero latency (**F-04**); costs are applied in the economic interpretation only |
| G5 | PARTIAL | Pooled file and governance entry exist; no run ID or method version |

**Does the negative support "no edge"? No.** "Edge not established" only.

## 8. Cross-sectional audit (`cross_section_engine`, `run_cross_sectional_oos`, `cross_sectional_permutation_test`, `oos_validator.evaluate_cross_sectional_oos`)

| Gate | Status | Evidence |
|---|---|---|
| G1 | PASS (with a history) | The fixed universe-hardcoding bug (09-13) is regression-tested; rebalance and turnover cost model |
| G2 | PASS | Ranks across columns per row; turnover measured as the actual book change; costs ×2 round trip |
| G3 | **FAIL** | **Null well designed** (within-timestamp shuffle of feature labels, keeping market moves and time structure), but default B = 100, and the FX "survivor" run used **about 20** (p = 1/21 floor), with a 4-way top-k sweep (**F-07**). Per-fold p-values are IID (**F-02**) |
| G4 | **FAIL** | **F-04:** signal at close t, label = return close t → t+1. The book trades at the signal close with no latency; for an hourly reversal signal this invites bid-ask-bounce artifacts. **F-10:** `UNIVERSE` is a hardcoded current list (survivorship) |
| G5 | PARTIAL | The family verdict JSON records a data fingerprint and cost rate; no code commit or run ID |

## 9. PCA audit (`pca_engine.py`)

| Gate | Status | Evidence |
|---|---|---|
| G1 | PASS | Runs; Kaiser + variance rule; reuses `RollingZScore` |
| G2 | PASS | Eigendecomposition of the standardized correlation matrix; residual = return − loadings·factors |
| G3 | FAIL | Residual IC with a B = 100 floor; the IC is in-sample on the same history |
| G4 | **FAIL (CRITICAL, F-01)** | `run_pca_engine` → `run_pca(returns)` estimates mean, std, correlation and loadings on the **entire** history, then measures residuals and IC on that history. **PCA1:** altering only t ≥ 2,000 changed residuals at t < 1,900 by up to 0.0040 |
| G5 | FAIL | Outputs INVALIDATED (registry) |

## 10. DCA audit (`dca_engine.py`, `dca_calibration.py`)

| Gate | Status | Evidence |
|---|---|---|
| G1 | PASS | 59 tests; 8/8 fix-reversal mutations caught; `LEGACY_CLOSE` reproduces the old results exactly |
| G2 | PASS | Ladder, weighted average, MTM, financing accrual, gap hours: known answers |
| G3 | **FAIL (F-08)** | Calibration (R = 200 / 100, B = 99, 2,000 candles). Size is controlled: iid 0.0 % / 0.5 %, GARCH 0.5 % / 1.0 %. **Power ≈ 0:** a planted AR(1) mean reversion is detected 0.0 % (σ = 0.004) and 1.0 % (σ = 0.008). The intersection-union rule on two statistics is very conservative, and the baseline ladder's response to the planted structure is weak relative to path noise. **As designed, the significance experiment cannot answer its question.** |
| G4 | PARTIAL | Canonical clock; two fill models bracket the deployment speed; borrow/swap labelled UNAVAILABLE; FX swap has no data; `r_multiple` = return on deployed capital fed to an R-Sharpe via `oos_validator` (**F-13**, M-06 open) |
| G5 | PARTIAL | Protocol document written before implementation; no run IDs; the BTC descriptive run is pending re-production at `ef5636b` |

## 11. Feature research audit (`feature_calculator`, `feature_validator`, stability, decay, correlation)

| Gate | Status | Evidence |
|---|---|---|
| G1 | PARTIAL | ADX dtype bug fixed (the July outputs are SUPERSEDED); `atr_indicator` duplicates `atr` |
| G2 | PASS | Indicator formulas read; label = forward return via `shift(-N)` |
| G3 | **FAIL** | **The authoritative block permutation is approximately calibrated: FV1 size 7.0 % at nominal 5 %** (block = horizon = 4, persistent feature, R = 300). But B = 100 against family-wide BH (**F-07**, H-12), so negatives are uninformative. The analytic `ic_pvalue` that is also reported is IID (**F-02**) |
| G4 | PARTIAL | Same-close label (**F-04**); "economically negligible" conclusions are cost-based and robust |
| G5 | PARTIAL | Labels in the dashboards are now badged LEGACY; no run provenance |

## 12. Regime research audit

| Gate | Status | Evidence |
|---|---|---|
| G1 | PASS | Detector returns a fully populated `RegimeResult`; the precompute matches per-candle detection (RG2: 0 mismatches) |
| G2 | PASS | ADX / ATR ratio / EMA spread / BB width formulas |
| G3 | PARTIAL | The interaction test is a sound gate; per-regime IC significance inherits **F-02** |
| G4 | PASS (component) | RG1: perturbing t ≥ 1,200 changed 0 earlier regimes; `regime_labels.py` uses fixed thresholds and causal EWMs (no full-sample quantiles found) |
| G5 | PARTIAL | Research-lab REGIME_DEPENDENT verdicts are UNVERIFIED |

No HMM component exists in the repository.

## 13. Backtesting / simulation audit

| Gate | Status | Evidence |
|---|---|---|
| G1 | PASS | Phase 3 tests 33/33; 8/8 defect reproductions; consumers regression 499 tests, 4 failures, all pre-existing network failures |
| G2 | PASS | Gap-aware exits, MTM liquidation drawdown, textbook Sharpe/Sortino (known answers) |
| G3 | PARTIAL | Daily Sharpe with no serial-correlation adjustment (lag-1 autocorrelation is reported); trade-based R ratios are labelled approximations |
| G4 | PARTIAL | Next-open fills, gap-aware exits, liquidation marks. **Not modelled:** bid/ask (slippage proxy only), market impact, borrow for shorts. **F-20:** `data_fetcher.get_klines` keeps the forming last candle, so dashboard backtests' final mark can use a partial candle |
| G5 | PARTIAL | Results carry `fill_model` / `ratio_basis` / `drawdown_basis` labels; no run ID; strategies gated (MA UNTESTED, MR REJECTED); **F-14:** scorer thresholds not re-validated for the new Sharpe basis, and Sortino `None` is coerced to 0 |

## 14. OOS / walk-forward audit

| Component | G1 | G2 | G3 | G4 | G5 | Evidence |
|---|---|---|---|---|---|---|
| `oos_validator` | PASS | PASS | **FAIL** | PASS | PARTIAL | Purge = horizon, embargo, exclusive train end; the fit function sees only the purged train slice. **Per-fold IC p = `spearmanr` (IID; F-02).** Aggregate t-test across folds is more defensible but low-n and fold-dependent. Trade-based Sharpe now without the rf unit error |
| `walk_forward_engine` | PASS | PASS | **FAIL** | PARTIAL | FAIL | **F-06 (M-03):** `test_start = train_end` (line 720), no purge; legacy rule set; legacy provenance sidecar |

## 15. Statistical infrastructure audit

| Procedure | B / floor | Calibration | Status |
|---|---|---|---|
| Pairs permutation (`permutation_test_engine`) | 100 / 0.0099 | none; z-timing null, not cross-asset (**F-05**) | FAIL for edge claims |
| Feature validator block permutation | 100 / 0.0099 | **size 7.0 % (FV1)** | Design PARTIAL; resolution FAIL vs families |
| Cross-sectional within-timestamp shuffle | 100 default (about 20 in the FX run) | none | Design sound; resolution FAIL |
| Research-lab conditional block permutation | 100 | none; `fdr_adjusted_p_value = block_p` (**F-11**) | FAIL (misleading field) |
| DCA nulls (Phase 2) | 999 default | **size ≤ 1 %, power ≈ 0 (F-08)** | Design must change |
| Engle–Granger + BH | analytic | **size 6 %, power 100 %** | PASS within a scan family |
| IID analytic p-values (Spearman / Pearson) | — | **67.7 % size on overlapping data (F-02)** | FAIL wherever used for decisions |
| Multiple testing across experiments / scans | — | — | Not controlled (**F-16**, research lab) |

## 16. Data / provenance audit

| Item | Finding |
|---|---|
| Integrity (446 files) | 0 duplicate timestamps, 0 unsorted, 0 unparseable, 0 non-positive closes. Gaps: forex weekends (expected), LUNA 438 h, STRAX 198 h. Funding / OI files are irregular by nature |
| **F-09 partial last candle** | **142 / 155 hourly files** were written 23:10–23:31 on 2026-09-24 with a 23:00 candle that closes at 24:00, so the last row is a **forming candle stored as final**. It affects data-end marks (DCA censored marks, pairs data-end closes), last-candle decisions, and the CRRP data-manifest fingerprints (they lock in partial rows) |
| Timestamp formats | 233 epoch, 213 ISO (funding/OI vs price); loaders handle both |
| Lineage | CRRP manifest (SHA-256) exists and is tracked; no per-download provenance in `data_fetcher` (unaudited module) |
| Experiment / run IDs | Exist only in Stage 2D canonical provenance and the research-lab model; absent from every other engine |
| Code provenance | `entry_exit_engine` records `code_commit` / `code_dirty`; most engines do not |

## 17. Other research modules found

| Module | Note |
|---|---|
| `derivatives_engine.py` | Funding / OI features; labels `shift(-periods)` forward; trailing stats. Not deep-audited (UNKNOWN) |
| `trade_flow_engine.py`, `orderbook_depth_engine.py`, `order_book_depth_check.py` | Microstructure measurement; the order-book measurement is a dated real observation (LEGACY / DESCRIPTIVE) |
| `regime_conditional_{ic,oos,pairs,permutation,status}` | Inherit F-02 / F-07 where p-values are IID or B = 100; not deep-audited |
| `parity_{replay,forensics,datasets}` | Stage 2D live-vs-research parity; canonical infrastructure; not re-audited here (UNKNOWN) |
| `cointegration_pipeline_runner.py`, `forex_cointegration_scan.py` | Drive the cointegration engine (inherit §5); FX BLOCKED |
| `strategy_oos_adapters.py`, `permutation_stats.py` | Shared helpers |
| `pairs_crrp_retest.py` (new, uncommitted) | Uses the **legacy seed (F-03)**, so its partial results are provisional, not evidence |
| `research_lab/*` | Verdict semantics (**F-11**); B = 100; experiments carry `code_version` / `data_fingerprint` fields, but pre-reset rows lack methodology |

---

## 18. Historical result classification (re-traced)

No historical result is upgraded. The CRRP registry classifications are **confirmed**, with these additions:

| Result | Class | Gates failed | Note |
|---|---|---|---|
| Pairs design attempts 1–9, 7-pair portfolio, permutation verdicts, walk-forward leaderboard | INVALIDATED | G2 (C-01, fixed only for new runs), G3 (C-03), G4 (legacy seed **F-03**), G5 | Confirmed |
| Corrected 09-29 DODO/FIDA and MINA/ONG negatives | LEGACY / DESCRIPTIVE | **also F-03** (legacy seed) | Direction plausible; magnitude uncertain |
| 2026-10-03 partial 10-pair retest (7 pairs; 6 net negative, DODO/FIDA UNSTABLE) | **UNVERIFIED** (provisional) | G4/G5: legacy seed **F-03**; no valid null | Stopped by the owner; not evidence |
| `cointegration_pairs.csv` | SUPERSEDED | G3/G5 | Confirmed |
| PCA residual IC | INVALIDATED | G4 (**F-01**, now proven) | Confirmed |
| FX cross-sectional survivor | INVALIDATED | G3 (B ≈ 20), G4 (**F-04**) | Confirmed |
| Crypto cross-sectional, contagion, rsi, regime negatives | LEGACY / DESCRIPTIVE | G3 (F-02 / F-07), G4 (F-04) | Economic negatives stand as description; **edge not established**, never "no edge" |
| Feature labels (July) | LEGACY / DESCRIPTIVE | G3 (F-07), G1 (ADX) | Confirmed |
| Pre-Phase-2 DCA | INVALIDATED | G2/G3/G4 | Confirmed |
| Phase 2 BTC DCA descriptive run | LEGACY / DESCRIPTIVE | G5 (pending re-production at `ef5636b`) + F-09 (its censored mark uses a partial last candle) | |
| DCA calibration (synthetic) | UNVERIFIED → can become CANONICAL (methodological) once re-run at `ef5636b` | — | Says nothing about any market |
| Research-lab experiments (46) | UNVERIFIED / INVALIDATED (pairs) | G3 (F-11), G5 | Confirmed |

## 19. Five-gate master scorecard

| Component | Python | Mathematical | Statistical | Quant / Trading | Research integrity | Overall |
|---|---|---|---|---|---|---|
| Engle–Granger test + BH (core) | PASS | PASS | PASS | N/A | PARTIAL | Component validated within a scan family |
| Cointegration engine (pipeline) | PASS | PASS | PARTIAL | PARTIAL | FAIL | Not validated |
| Kalman filter (canonical) | PASS | PASS | N/A | PASS | PARTIAL | Component validated; provenance partial |
| Kalman (legacy seed path) | PASS | PASS | N/A | **FAIL** | FAIL | **Invalid for evidence** |
| Pairs P&L (`pnl.py`, `entry_exit` C-01) | PASS | PASS | N/A | PARTIAL (borrow = 0 when unspecified) | PARTIAL | Component validated; economics incomplete |
| Pairs lifecycle (Stage 2D) | PASS | PASS | N/A | PASS | PARTIAL | Component validated |
| Pairs permutation engine | PASS | PASS | **FAIL** | PARTIAL | FAIL | Not validated |
| Walk-forward engine | PASS | PASS | **FAIL** | PARTIAL | FAIL | Not validated |
| OOS validator | PASS | PASS | **FAIL** | PASS | PARTIAL | Not validated |
| Feature validator | PARTIAL | PASS | **FAIL** | PARTIAL | PARTIAL | Not validated |
| Contagion | PASS | PASS | **FAIL** | PARTIAL | PARTIAL | Not validated |
| Cross-sectional | PASS | PASS | **FAIL** | **FAIL** | PARTIAL | Not validated |
| PCA | PASS | PASS | FAIL | **FAIL** | FAIL | **Invalid** |
| Regime detector | PASS | PASS | N/A | PASS | PARTIAL | Component validated |
| Regime research | PASS | PASS | PARTIAL | PASS | PARTIAL | Not validated |
| DCA simulator | PASS | PASS | **FAIL** (design power) | PARTIAL | PARTIAL | Simulator validated; significance design not |
| Backtester (Phase 3) | PASS | PASS | PARTIAL | PARTIAL | PARTIAL | Mechanics validated; not evidence-producing |
| Strategy scorer | PASS | PARTIAL | PARTIAL | PARTIAL | PARTIAL | Not validated |
| Research lab verdicts | PASS | PASS | **FAIL** | FAIL | PARTIAL | Not validated |
| Data layer / price files | PASS | N/A | N/A | PARTIAL (**F-09**) | PARTIAL | Not validated |
| Evidence registry / dashboards | PASS | N/A | N/A | N/A | PASS | Validated |
| Derivatives / trade-flow / order-book engines | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | Not audited |
| Parity modules (Stage 2D) | UNKNOWN | UNKNOWN | N/A | UNKNOWN | UNKNOWN | Not re-audited |

## 20. Critical and high findings

| ID | Sev. | File · function | Exact problem | Gate | Evidence | Consequence | Affects history | Remediation | Owner? |
|---|---|---|---|---|---|---|---|---|---|
| F-01 | **CRITICAL** | `pca_engine.py` · `run_pca_engine` / `run_pca` | Mean, std, correlation and loadings fitted on the full history; residual IC on the same history | G4 / G5 | PCA1: Δresidual (t < 1,900) = 0.0040 after altering t ≥ 2,000 | Residual IC uses the future | Yes: PCA results INVALIDATED (already) | Rolling or expanding fit on data ≤ t; refit schedule declared | No (spec choice of window: yes) |
| F-02 | **CRITICAL** | `oos_validator.py` l.530 (`spearmanr`); contagion `hypothesis_7`; feature-validator `ic_pvalue` (reported) | IID p-values on overlapping or serially dependent labels | G3 | M4: 67.7 % size at nominal 5 % | False "significance" | Yes: every IID-p claim | HAC (Newey–West, lag ≥ horizon) or block bootstrap; demote IID p to descriptive | Spec: yes |
| F-03 | **CRITICAL** | `kalman_filter_engine.py` legacy seed via `load_pair_config` / explicit seed; `entry_exit_engine` LEGACY / CANONICAL_UNCERTIFIED; research-lab pairs tool; `pairs_crrp_retest.py` | θ₀ = OLS over the first 10,000 candles, applied from t = 0 | G4 / G5 | K3: β = −0.856 vs true 0.8 | Early-sample state uses the future; any run on legacy Kalman CSVs is contaminated | Yes: all legacy pairs; yesterday's retest | Use `run_canonical` / `causal_init` (L = 1000) everywhere; refuse legacy CSVs for evidence | Zone S (yes) |
| F-04 | HIGH | `cross_section_engine._calculate_forward_returns`; `contagion_engine._calculate_forward_returns`; feature labels | Signal at close t, label from close t, so execution at the signal print with zero latency | G4 | Code (`shift(-1)` / `shift(-N)` from T+1 with features through T) | Bid-ask bounce / zero-latency inflation, especially for reversal signals | Yes (cross-sectional, contagion, features) | Label from the next open (or t+d) with a declared d; re-label | Spec: d (yes) |
| F-05 | HIGH | `permutation_test_engine._block_shuffle` | Shuffles z timing, not the cross-asset relationship; B = 100 | G3 | Code; C-03 | Cannot test a pairs edge | Yes (verdicts INVALIDATED) | Stage 2D Step 17 null (independent-leg or circular-shift bootstrap, ≥ 999, calibrated) | Yes (Step 17 approval) |
| F-06 | HIGH | `walk_forward_engine.py` l.720 | `test_start = train_end`, no purge / embargo | G3 / G5 | Code | Train/test share a candle; horizon overlap leaks | Yes (legacy walk-forward) | Exclusive boundary; purge = horizon + d; embargo | No |
| F-07 | HIGH | `feature_validator` (B = 100), `cross_sectional_permutation_test` (100; FX run ≈ 20), research-lab tools (100), pairs (100) | Floor 1/(B+1) above BH/Bonferroni thresholds for realistic families | G3 | Arithmetic; FX p = 1/21 for all configurations | Negatives uninformative; floor-level "passes" | Yes | B ≥ m/(q·k) − 1 per pre-declared family, or a two-stage design | Yes (family spec) |
| F-08 | HIGH | `dca_engine.run_dca_permutation_test` design | ≈ 0 power under a planted effect | G3 | Calibration JSON: power 0.0 / 1.0 % | The experiment cannot detect what it tests | No (not yet run) | Redesign: statistic, rule, sample length, effect definition; re-calibrate before running | **Yes** |
| F-10 | HIGH | `cross_section_engine.UNIVERSE`; cointegration scan universes | Current-listing universes over 2021–2026 | G4 / G5 | Code | Survivorship bias | Yes | Point-in-time universe with listing/delisting dates | Yes (data scope) |
| F-11 | HIGH | `research_lab/verdict.py`; `tools/conditional_tools.py` l.222 | SUPPORTED without costs / OOS / economic test; `fdr_adjusted_p_value = block_p` | G3 / G5 | Code | Labels overstate evidence; field mislabelled | Yes (46 experiments UNVERIFIED) | Rename / compute a real family correction; require cost + OOS gates for SUPPORTED | Yes (vocabulary) |
| F-16 | HIGH | Cointegration scan workflow | Separate BH per scan; repeated scans on the same exposed data | G3 / G5 | Governance log / exposure history | Family-wise inflation; selection bias | Yes | Registered scan family; zone-S-only selection | Yes |

## 21. Medium and low findings

| ID | Sev. | Where | Problem | Remediation |
|---|---|---|---|---|
| F-09 | MEDIUM (HIGH for data-end metrics) | `data/*_1h.csv` (142 / 155) | Partial last candle stored as final; fingerprinted in the CRRP manifest | Drop candles with close time > write time at ingestion; re-fetch; new manifest version |
| F-12 | MEDIUM | `entry_exit_engine` | Borrow charged 0 when no rate is given (warned) | Fail-closed or label `cost_complete=False` in summaries |
| F-13 | MEDIUM | `dca_engine._close_cycle` → `oos_validator` Sharpe | `r_multiple` = return on deployed capital used as R | Separate metric name; exclude from R-Sharpe |
| F-14 | MEDIUM | `strategy_scorer.py` | Thresholds unvalidated for the new Sharpe basis; Sortino None → 0 | Re-specify thresholds; treat undefined as undefined |
| F-15 | MEDIUM | `STAGE2D_DECISION_LOCK.md` C6; my earlier reports | Docs say L BLOCKED; code is locked at L = 1000 | Supersession note in the lock; correct the reports |
| F-17 | MEDIUM | `bot/pairs/health.py` | Drift measured vs the legacy seed (M-11) | Measure vs the canonical seed |
| F-19 | MEDIUM | `cointegration_engine.py` (~l.334, 754, 1057) | "Untouched holdout" naming for a pseudo-holdout ADF | Rename the fields |
| F-20 | MEDIUM | `data_fetcher.get_klines` | Keeps the forming last candle (dashboard backtests) | Closed-candle filter (as in H-07) |
| F-18 | LOW | `kalman_filter_engine` writer | Timestamp column written as `index` | Fixed (uncommitted, one line) |
| F-21 | LOW | Feature calculator | `atr_indicator` ≡ `atr` (duplicate feature) | Remove or document |

## 22. Cross-module dependencies (how defects propagate)

| Upstream | Propagates to |
|---|---|
| F-03 legacy seed | Kalman CSVs → `entry_exit` (LEGACY / UNCERTIFIED) → `permutation_test_engine`, `walk_forward_engine`, the research-lab pairs tool, the retest → dashboards (now badged) |
| F-02 IID p-values | `oos_validator` per-fold → regime-conditional OOS / IC → research-lab verdicts → contagion `hypothesis_7` |
| F-07 B = 100 | `feature_validator` → contagion validation, `validated_feature_registry`, the MeanReversion verdict (rejection fail-safe) |
| F-04 same-close labels | cross-sectional / contagion / feature research → `oos_validator` cross-sectional evaluation |
| F-09 partial candle | any data-end mark (DCA censored marks, pairs data-end close), CRRP manifest fingerprints |
| H-11 basis change | `oos_validator` trade-based ratios, `strategy_scorer`, `portfolio_backtester` rankings |

## 23. Canonical research readiness

| Engine | Readiness |
|---|---|
| Pairs (Stage 2D canonical) | **BLOCKED: SPECIFICATION REQUIRED** (zones S/V/WF/F, forward holdout F) and **IMPLEMENTATION REQUIRED** (Step 17 null, eligibility producer) |
| Pairs legacy research (LEGACY / UNCERTIFIED, permutation, walk-forward) | **NOT CANONICAL-CAPABLE** (F-03, F-05, F-06) |
| Cointegration selection | **BLOCKED: SPECIFICATION REQUIRED** (zone S; registered scan family) |
| Kalman (canonical component) | **BLOCKED: PROVENANCE REQUIRED** (seed lineage needs zone S placement) |
| DCA | **BLOCKED: STATISTICAL DESIGN REQUIRED** (F-08) |
| Feature research | **BLOCKED: STATISTICAL DESIGN REQUIRED** (F-07, F-02) and **IMPLEMENTATION** (F-04 labels) |
| OOS validator | **BLOCKED: IMPLEMENTATION REQUIRED** (dependence-aware inference) |
| Walk-forward | **BLOCKED: IMPLEMENTATION REQUIRED** (F-06) |
| PCA | **NOT CANONICAL-CAPABLE** until a causal fit exists (**BLOCKED: IMPLEMENTATION REQUIRED**) |
| Cross-sectional | **BLOCKED: IMPLEMENTATION REQUIRED** (F-04, F-10) and **STATISTICAL DESIGN REQUIRED** (F-07) |
| Contagion | **BLOCKED: STATISTICAL DESIGN REQUIRED** (F-07, F-02) and **IMPLEMENTATION REQUIRED** (F-04) |
| Regime research | **BLOCKED: STATISTICAL DESIGN REQUIRED** (F-02) |
| Backtester / main strategies | **BLOCKED: PROVENANCE REQUIRED** (run IDs, registration); strategies gated |
| Research lab | **NOT CANONICAL-CAPABLE** (F-11) |
| Data layer | **BLOCKED: IMPLEMENTATION REQUIRED** (F-09 ingestion fix, re-fetch, new manifest) |

## 24. Recommended remediation order (not started)

1. **F-09:** closed-candle ingestion, re-fetch, new manifest version. Cheap, and everything depends on the data.
2. **F-03:** route all pairs research through `causal_init`; refuse legacy Kalman CSVs for evidence. Re-run the 10-pair retest only after this.
3. **F-02:** dependence-aware inference in `oos_validator` and wherever IID p-values feed verdicts.
4. **F-06:** walk-forward purge and exclusive boundary.
5. **F-04:** declared execution delay in cross-sectional, contagion and feature labels.
6. **F-01:** causal PCA.
7. **F-07 / F-16:** family registration and family-sized B (or a two-stage design).
8. **F-05:** Stage 2D Step 17 null, calibrated (size on unrelated random-walk pairs, power on synthetic cointegration).
9. **F-08:** DCA test redesign, then re-calibration.
10. **F-10:** point-in-time universes.
11. **F-11, F-12, F-13, F-14, F-15, F-17, F-19, F-20, F-21.**

## 25. Tests performed (this audit; all offline)

| ID | Test | Result |
|---|---|---|
| K1 | Kalman known-answer β/α recovery | β 1.501, α 0.19 (true 1.5 / 0.2) |
| K2 | Kalman causality (perturb t ≥ 4,000) | Past θ and z byte-identical |
| K3 | Legacy 10k-OLS seed vs causal L = 1000 on a β break | −0.856 vs 0.802 (true 0.8) |
| K4 | Protocol constants | Q 1e-5 / 1e-5, R 1e-3, P₀ 1, z 504 / 168, L 1000 |
| EG1–4 | Engle–Granger size, power, BH | 0.060 / 1.00 / 1 false of 200, 50 true of 50 |
| C01 | `test_pairs_pnl_identity` (7) | OK (incl. the random-walk null) |
| M4 | IID Spearman on overlapping windows | 67.7 % size |
| PCA1 | PCA future perturbation | Past residuals changed (0.0040) |
| FV1 | Feature-validator block-permutation size | 7.0 % |
| RG1–2 | Regime precompute causality / equivalence | 0 changes / 0 mismatches |
| D1 | Integrity scan, 446 files | 0 duplicates / unsorted / bad closes; gaps listed |
| D2 | Partial-last-candle scan | 142 / 155 hourly files |
| Prior | DCA calibration (7 scenarios) | Size ≤ 1 %, power ≈ 0 |
| Prior | Consumers regression | 499 tests, 4 failures (pre-existing network) |

## 26. Tests NOT performed (coverage limits)

- **Full regression:** the full test-by-test suite against `HEAD` after the uncommitted changes.
- **Not deep-audited:**
  - `derivatives_engine`, `trade_flow_engine`, the order-book engines;
  - the parity modules;
  - `quant_core/provenance` internals;
  - the live `signal_engine` vs research parity;
  - the research-lab orchestrator end to end;
  - `data_fetcher` code;
  - the main strategies' logic.
- **Calibration not run:**
  - size/power of the cross-sectional within-timestamp null;
  - the contagion validation null;
  - the walk-forward engine.
- **Sensitivity not tested:** Kalman Q/R; the canonical H = 24 / entry threshold.
- **Not quantified:**
  - survivorship bias size (F-10);
  - the economic impact of zero-latency labels (F-04);
  - borrow-cost impact on pairs.
- **Not re-run (by instruction):** the full DCA significance experiment and any canonical Stage 2D research run.

## 27. Open decisions requiring owner approval

1. **Zones S / V / WF / F and the forward-holdout start** (unblocks canonical pairs selection, seed placement and claim-eligible OOS).
2. **Execution delay d for the feature, cross-sectional and contagion labels** (F-04).
3. **Inference standard for dependent data:** HAC vs block bootstrap, and lag rules (F-02).
4. **Family definitions and the B / two-stage design** for each research program (F-07, F-16).
5. **DCA significance redesign** (statistic, decision rule, sample length, effect definition) before any run (F-08).
6. **Stage 2D Step 17 approval** (canonical pairs null) (F-05).
7. **Point-in-time universe scope and the data source** for listings/delistings (F-10).
8. **Re-fetch the data** to remove partial candles, and version a new data manifest (F-09).
9. **Research-lab verdict vocabulary:** what SUPPORTED must require (F-11).
10. **Commit** the pending one-line Kalman writer fix (F-18) and the retest module, or discard the retest module until F-03 is fixed.

## Final research governance status

**Edge established: none. Evidence for every strategy family: invalidated, insufficient, or blocked.** The infrastructure now has validated components:
- Engle–Granger test;
- canonical Kalman;
- pairs P&L;
- lifecycle;
- regime detector;
- backtester mechanics;
- DCA simulator;
- evidence governance.

It is **not yet capable of producing canonical evidence**. The three critical defects (F-01 PCA look-ahead, F-02 IID inference on dependent data, F-03 legacy-seed look-ahead), together with the high-severity design gaps above, must be closed and owner specifications supplied first.
