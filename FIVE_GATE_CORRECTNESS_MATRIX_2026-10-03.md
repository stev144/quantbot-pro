# Five-Gate Correctness Matrix (2026-10-03, after remediation batch 1)

**Gates:**
1. Software / Python
2. Mathematics
3. Statistics
4. Quant / Trading
5. Research integrity

**Cell values:**
- **PASS:** verified this batch with evidence.
- **FAIL:** a known defect remains.
- **BLOCKED:** needs an owner decision or data.
- **N/A:** the gate does not apply.

A cell can't be PASS on the strength of a code comment; evidence is cited in the dossiers below.

## A. Engine matrix

| Engine | Python | Math | Statistics | Quant/Trading | Research Integrity | Status |
|---|---|---|---|---|---|---|
| Data layer (`data_fetcher`, `data/` v2) | PASS (fetch rule; 4 tests) | PASS (completed-candle rule t ≥ open + Δ) | N/A | PASS (no forming candle enters a signal) | PASS (v2 manifest, v1 archived byte-identical, governance-log entry) | **CANONICAL-CAPABLE (data)**. Residual: the stale-cache fallback can serve expired caches (pre-existing, logged) |
| Kalman (`quant_core` canonical, `run_canonical`) | PASS | PASS (β recovery 1.3; future-shock invariance) | N/A | PASS (causal L = 1000 init) | PASS (lineage CANONICAL in sidecar) | **CANONICAL-CAPABLE** |
| Kalman legacy `run()` | PASS | PASS (filter recursion) | N/A | FAIL (θ₀ from first 10,000 candles is look-ahead) | PASS (now self-declares LEGACY_OLS_FIRST_N, seed_lookahead = true) | **LEGACY_DESCRIPTIVE**; kept for history only |
| Entry/exit lifecycle | PASS | PASS | N/A | PASS for costs. Borrow is now explicitly ASSUMED_ZERO_BORROW unless given | PASS (`kalman_seed_lineage` and `borrow_status` in every summary) | CANONICAL-CAPABLE **only with a CANONICAL seed and a known borrow** |
| Cointegration (Engle–Granger + BH) | PASS | PASS | PASS (calibrated: 6 % size, 100 % power) | N/A | BLOCKED (zones S/V/WF/F, D-8) | Usable for screening; selection BLOCKED |
| Pairs permutation (legacy z-timing null) | PASS | PASS | FAIL (null preserves the relationship: F-05) | FAIL | BLOCKED (Step 17 spec, D-4) | **INVALID for edge claims** |
| Pairs CRRP retest module | PASS (smoke test on the canonical path) | PASS | BLOCKED (inherits F-05 for any p-value) | PASS (causal seed, rolled-return controls) | PASS (amendment 2 logged in module) | Ready to run **only after owner approval**; not run |
| Walk-forward engine | PASS (M-03 fixed; 18 tests) | PASS (disjoint folds) | BLOCKED (OOS significance inherits F-02/F-07) | FAIL (folds call the legacy Kalman seed) | PASS (lineage now flags it) | LEGACY_DESCRIPTIVE until moved to `run_canonical` |
| Feature / IC engines | PASS | PASS | FAIL (IID p on overlapping labels: F-02). A fix is calibrated, not wired | FAIL (d = 0 labels: F-04). Contract built, not wired | BLOCKED (D-1, D-2) | **LEGACY_DESCRIPTIVE** |
| Cross-sectional ranking | PASS | FAIL (full-history PCA: F-01) | FAIL (F-02, F-07) | FAIL (F-04, F-10 survivorship) | BLOCKED (D-1, D-2, D-3, D-6, D-9) | **LEGACY_DESCRIPTIVE** |
| Contagion engine | PASS | PASS | FAIL (B = 100 vs m = 2,156: F-07) | FAIL (F-04) | BLOCKED (D-3) | Prior RESEARCH_NEGATIVE stays descriptive |
| DCA engine | PASS (M-06 fixed) | PASS (B1–B8) | FAIL (statistic does not respond to the planted effect: F-08) | PASS (MTM / liquidation accounting) | BLOCKED (D-5) | **Descriptive only** |
| Strategy scorer | PASS (F-14 fixed; 3 tests) | PASS (undefined ≠ 0) | N/A | BLOCKED (thresholds need re-specification) | PASS | Usable; thresholds pending |
| Backtester R-ratios | PASS (M-06) | PASS (R-only, None if no R) | N/A | PASS | PASS | OK |
| Research-lab conditional tool | PASS | PASS | PASS (no false family claim; raw block p kept) | N/A | BLOCKED (verdict tiers: D-7; family registry: F-16) | Field honest; verdict vocabulary pending |
| Inference candidates (`quant_core/inference`) | PASS | PASS | PASS for size (block / circular ≈ nominal; HAC 7–10 %, documented) | N/A | PASS (artifact registered UNVERIFIED) | Ready; **not wired (D-2)** |
| Timing contract (`quant_core/timing`) | PASS (4 tests) | PASS (known-answer label) | N/A | PASS (bounce demo) | PASS | Ready; **not wired (D-1)** |

## B. Per-issue verification dossiers

---
### F-09: partial last candle in stored 1h files
- **Issue:** The stored 1h CSVs ended in a candle that was still forming when the file was written.
- **Root cause:**
  - Files were written mid-hour, and `_build_ohlcv_dataframe` had no completed-candle rule.
  - Binance returns the forming kline with `close_time` in the future.
- **Affected components:** `bot/data_fetcher.py`, every reader of `data/*_1h.csv` and `data/forex/*_1h.csv`.
- **Gate 1 PASS:**
  - Every fetch path goes through `_build_ohlcv_dataframe(raw, as_of_ms)`, which drops rows with `close_time ≥ as_of`.
  - The correction tool asserts the archive copy's sha256 equals the original's.
- **Gate 2 PASS:**
  - Rule: complete ⇔ t_fetch ≥ open + Δ.
  - For files, mtime bounds t_fetch from above. A 60 s margin around the boundary is classed UNVERIFIABLE and treated as partial (conservative).
- **Gate 3 N/A.**
- **Gate 4 PASS:** No signal can read the forming bar's high/low/close.
- **Gate 5 PASS:**
  - Versioned rather than overwritten: v1 is archived in `data/_archive/v1_CRRP-2026-10-02/`.
  - The v2 manifest (`data_manifest_CRRP-2026-10-02_v2.json`) records PARTIAL 142, UNVERIFIABLE 3 (BNT, GNO, QTUM) and COMPLETE 68.
  - The governance log has an append-only entry; history is byte-identical.
- **Historical impact:**
  - One row per file, at the end of the series.
  - It matters for "latest-signal" outputs and the last label. It's immaterial to long-sample statistics, but those results are pre-v2 and so not reproducible on v2 byte-for-byte.
- **Governance:** pre-v2 results → LEGACY_DESCRIPTIVE (already below the CRRP reset).
- **Regression test:** `bot/tests/test_data_integrity.py` (4):
  - boundary classification;
  - byte-exact correction for LF, CRLF and trailing-newline variants;
  - a complete file left untouched;
  - the fetch rule.

---
### F-03: Kalman seed look-ahead
- **Issue:** Legacy pairs research seeded the Kalman filter with information from the future.
- **Root cause:** The legacy `run()` takes θ₀ from an OLS over the first 10,000 candles and starts filtering at t = 0. The output had no lineage, so `CANONICAL_UNCERTIFIED` consumed it silently.
- **Affected components:** `kalman_filter_engine.run`, `entry_exit_engine`, `pairs_crrp_retest`, and legacy consumers (cointegration_pipeline_runner, the research-lab pairs tool, permutation/walk-forward engines).
- **Gate 1 PASS:**
  - Sidecar write is guarded (OSError).
  - No sidecar → lineage UNKNOWN (not assumed canonical).
- **Gate 2 PASS:**
  - Canonical path: shocking prices after the init window leaves every earlier state bit-identical.
  - Synthetic β = 1.3 is recovered.
- **Gate 3 N/A.**
- **Gate 4 PASS (canonical path):** The retest uses `run_canonical` (L = 1000 causal init) and `rule_set="CANONICAL"`. Controls use rolled log-returns, not spliced price levels.
- **Gate 5 PASS:**
  - Legacy outputs self-declare `LEGACY_OLS_FIRST_N, seed_lookahead=true`.
  - The legacy method is kept, not deleted.
  - Amendment 2 is recorded in the retest module.
- **Historical impact:**
  - Every legacy pairs result: the 10/7-pair portfolios, the 100-coin scan and the earlier pilot.
  - The look-ahead is in the initial state only. It decays through the filter but contaminates the early window and the selection.
- **Governance:** INVALIDATED as evidence of an edge (already LEGACY_DESCRIPTIVE); rerun required on the canonical path.
- **Regression test:** `bot/tests/test_kalman_seed_lineage.py` (6), mutation-checked:
  - the legacy sidecar;
  - canonical lineage;
  - UNKNOWN when there is no sidecar;
  - future-shock invariance;
  - β recovery;
  - a retest smoke test.

---
### F-02: IID inference on overlapping labels
- **Issue:** Significance tests treated overlapping labels and persistent features as independent observations.
- **Root cause:** Spearman p-values assume independent pairs. h-bar forward labels are MA(h−1), and features are persistent.
- **Affected components:** feature validator / IC engines, cross-sectional, regime and research-lab tests that use IID p-values.
- **Gate 1 PASS:** The `quant_core/inference.py` functions are tested by the calibration run.
- **Gate 2 PASS:**
  - HAC: OLS on ranks with Bartlett-kernel standard errors.
  - Circular shift and block permutation preserve the dependence.
  - p = (1 + #≥)/(B + 1).
- **Gate 3 PASS (candidates) / FAIL (engines):**
  - IID size reaches 60 % at h = 24, φ = 0.99.
  - Block and circular shift come out at 2.7–8.3 %; HAC at 7–10 %.
  - Full table in the plan, §3.
- **Gate 4 N/A.**
- **Gate 5 BLOCKED:** The method choice is D-2. Not wired, so no engine output changes silently.
- **Historical impact:** Every IID-based significance claim in feature, cross-sectional and contagion research is anti-conservative, possibly by a large factor.
- **Governance:** those claims → LEGACY_DESCRIPTIVE / UNVERIFIED.
- **Regression test:** `research/inference_calibration.py` (reproducible from fixed seeds) plus its registered artifact. A unit test will be added once a method is chosen and wired.

---
### F-04: zero-latency labels
- **Issue:** Research labels assumed the trade filled at the same price that generated the signal.
- **Root cause:** Labels were built close_t → close_{t+h} from the signal bar, i.e. a fill at the print that formed the signal.
- **Affected components:** feature, cross-sectional and contagion label builders.
- **Gate 1 PASS:**
  - `TimingContract` has no default d.
  - d = 0 is refused unless explicitly diagnostic.
  - d = 0 with open prices is rejected as impossible.
- **Gate 2 PASS:** Known answer: label = open[t+1+h] / open[t+1] − 1.
- **Gate 3 N/A.**
- **Gate 4 PASS (contract):**
  - Bid-ask bounce demonstration: same-close IC 0.157 (analytic ≈ 0.167) vs next-open 0.014.
  - The label never reads a close under an open-price contract.
- **Gate 5 BLOCKED:** Canonical d is D-1; the contract is not wired.
- **Historical impact:**
  - Short-horizon reversal/IC results are inflated by bounce and latency.
  - It's most severe at h = 1; the forex top_k = 1 "ECONOMICALLY_SUPPORTED" result is affected.
- **Governance:** LEGACY_DESCRIPTIVE; rerun with d ≥ 1 required.
- **Regression test:** `bot/tests/test_timing_contract.py` (4).

---
### F-05 / F-07: permutation null and resolution
- **Issue:** Permutation tests either didn't test the edge hypothesis (pairs) or couldn't resolve small enough p-values for their family sizes.
- **Root cause:**
  - The legacy pairs null permutes z-timing while preserving the cointegration relationship, so it doesn't test the edge hypothesis.
  - B = 100 everywhere gives p ≥ 0.0099, which can't pass correction for large m.
- **Affected components:** `permutation_test_engine`, contagion, DCA, research-lab tools.
- **Gate 1 N/A** (no code change this batch).
- **Gate 2 PASS (specification):** B ≥ m/(α·k) − 1 (plan §3).
- **Gate 3 FAIL** in current engines.
- **Gate 4 FAIL** (pairs null).
- **Gate 5 BLOCKED** (D-3, D-4; Step 17 needs approval).
- **Historical impact:** Every pairs permutation "pass" is uninformative about the edge. Contagion's 0 KEEP stays a descriptive negative.
- **Governance:** INVALIDATED (pairs edge claims); LEGACY_DESCRIPTIVE (others).
- **Regression test:** none yet (blocked).

---
### F-08: DCA significance
- **Issue:** The DCA significance test cannot detect an effect even when one is planted.
- **Root cause:**
  - The MTM statistic does not respond to the planted effect: mean p is 0.52–0.55 under H₁ vs 0.48–0.54 under H₀.
  - The effect is small relative to the ladder scale and the noise of a 2,000 h sample.
- **Affected components:** `dca_engine` significance path, `dca_calibration`.
- **Gates 1–2 PASS** (engine accounting; B1–B8).
- **Gate 3 FAIL.**
- **Gate 4 PASS** (MTM / liquidation accounting).
- **Gate 5 BLOCKED** (D-5). The full experiment was not run, as instructed.
- **Historical impact:** No DCA significance claim exists or can exist under the current design.
- **Governance:** descriptive only.
- **Regression test:** `dca_calibration.py` (committed) reproduces the diagnosis.

---
### F-10: survivorship / point-in-time universes
- **Issue:** Research universes contain only the coins that survive today.
- **Root cause:** Current listings are applied to 2021+ history.
- **Gate 5 BLOCKED:**
  - No authoritative listing/delisting data on disk.
  - Not fabricated, per instruction.
- **Historical impact:** Cross-sectional and multi-symbol scans are survivorship-biased (upward for momentum/long-only, unknown sign for ranking spreads).
- **Governance:** LEGACY_DESCRIPTIVE.
- **Regression test:** none (blocked on D-6).

---
### F-11: mislabeled "FDR-adjusted" field
- **Issue:** The research lab reported a family correction that was never applied.
- **Root cause:**
  - `fdr_adjusted_p_value = block_p` (single test, no family).
  - The template rendered None as 0.
- **Affected components:** `research_lab/tools/conditional_tools.py`, `templates/research_lab/results.html` and `report.html`.
- **Gate 1 PASS.**
- **Gate 2 PASS.**
- **Gate 3 PASS:**
  - The field is None; `family_correction = NOT_APPLIED_NO_REGISTERED_FAMILY`.
  - The raw block p is kept unchanged.
- **Gate 4 N/A.**
- **Gate 5 PASS** for the field; **BLOCKED** for the verdict tiers (D-7).
- **Historical impact:** Earlier research-lab reports overstated the evidence level (a single-test p presented as FDR-adjusted). The numeric p was correct.
- **Governance:** those reports → UNVERIFIED (family claim withdrawn).
- **Regression test:** `bot/tests/test_research_lab_statistical_integrity.py` (asserts None / NOT_APPLIED).

---
### F-16: repeated search / multiple testing
- **Issue:** Multiple-testing correction never accounts for searches repeated across scans.
- **Root cause:** BH is applied per scan, and there is no registry of the cumulative hypothesis family.
- **Gate 3 FAIL.**
- **Gate 5 BLOCKED** (D-3). A specification is proposed: a cumulative family registry per research program, with every executed test appended and corrections over the cumulative m.
- **Historical impact:** The cumulative false-discovery rate across repeated scans is uncontrolled.
- **Governance:** individual scan "survivors" → LEGACY_DESCRIPTIVE.
- **Regression test:** none yet.

---
### M-03: walk-forward boundary overlap
- **Issue:** The boundary candle appeared in both the training and test slices of each fold.
- **Root cause:** `test_start = train_end` with inclusive `.loc` slicing put the boundary candle in both slices, and in the fold's OLS seed.
- **Gate 1 PASS.**
- **Gate 2 PASS:** `test_start` = the first candle > `train_end`; `test_end` is anchored on `train_end` (boundaries don't drift).
- **Gate 3 N/A.**
- **Gate 4 PASS:** no shared candle.
- **Gate 5 PASS.**
- **Historical impact:** one candle per fold; numerically negligible, but a leakage violation.
- **Governance:** past walk-forward → LEGACY_DESCRIPTIVE (it also uses the legacy seed).
- **Regression test:** `bot/tests/test_walk_forward_engine.py` (18 pass; asserts disjoint slices and next-candle start).

---
### F-14: undefined Sharpe/Sortino scored as 0
- **Issue:** Strategies with too few trades for a ratio were scored "Poor" instead of "undefined".
- **Root cause:** None was coerced to 0 and scored "Poor".
- **Fix:** `sharpe_defined` / `sortino_defined` flags; undefined → a note, no points.
- **Gates:**
  - Gate 1 PASS.
  - Gate 2 PASS.
  - Gate 3 N/A.
  - Gate 4 BLOCKED (thresholds need re-specification).
  - Gate 5 PASS.
- **Historical impact:** Scores of low-trade-count strategies were mis-stated.
- **Governance:** LEGACY_DESCRIPTIVE.
- **Regression test:** `bot/tests/test_phase3_engineering.py::UndefinedRatioTests` (3).

---
### M-06: DCA "r_multiple" was not an R-multiple
- **Issue:** DCA trades reported a figure as an R-multiple that was actually return on deployed capital.
- **Root cause:** The DCA engine stored return on max deployed capital as `r_multiple`, and the backtester's Sharpe/Sortino consumed it as R.
- **Fix:**
  - `r_multiple=None`; the value moved to `return_on_max_deployed`.
  - R ratios use only R-bearing trades, return None if there are none, and annualise by the R-bearing count.
- **Gates:**
  - Gate 1 PASS.
  - Gate 2 PASS.
  - Gate 3 N/A.
  - Gate 4 PASS.
  - Gate 5 PASS.
- **Historical impact:** DCA Sharpe/Sortino figures were dimensionally wrong.
- **Governance:** INVALIDATED (those ratios).
- **Regression test:** `test_phase3_engineering` and the DCA suites (95 OK combined).

---
### F-12: pairs borrow status
- **Issue:** Pairs results charged zero borrow cost without saying so in the borrow vocabulary.
- **Root cause:** Borrow was charged at 0 when no rate was given. It was flagged, but outside the BORROW_KNOWN / BORROW_UNAVAILABLE / ASSUMED_ZERO_BORROW vocabulary.
- **Fix:** Every entry/exit summary carries `borrow_status`.
  - BORROW_UNAVAILABLE needs a real availability source, which is not invented.
  - Until one exists, ASSUMED_ZERO_BORROW is reported.
- **Gates:**
  - Gate 1 PASS.
  - Gate 2 N/A.
  - Gate 3 N/A.
  - Gate 4 PASS (honest assumption).
  - Gate 5 PASS.
- **Historical impact:** Every legacy pairs P&L is ASSUMED_ZERO_BORROW, which overstates net P&L on the short leg.
- **Governance:** LEGACY_DESCRIPTIVE.
- **Regression test:** covered via the entry/exit summary in `test_kalman_seed_lineage` (retest smoke test).
