# Pre-Phase-3 Research Governance Reassessment (2026-10-02)

**Mode:** read-and-classify. **No research result was deleted, rerun or modified.**

**One new artifact:** `research_data/_governance/data_manifest_CRRP-2026-10-02.json`, a SHA-256 fingerprint of all 446 price files, written for data lineage.

**Not done (as instructed):**
- Phase 3 was not started.
- Nothing traded.
- No holdout touched.
- Nothing committed.

**Method:**
- Every important historical result was traced to the code and artifact that produced it.
- Each one was checked against the findings that apply to it: the 2026-09-29 forensic audit (33 findings), the 2026-10-02 status audit, the Stage 2D lock, and Phase 1/2.
- Where a claim could be re-derived cheaply from the artifact itself, it was. Three examples:
  - FX cross-sectional p-values: 1/21;
  - PCA fitted on the full sample (`pca_engine.py:636–645`);
  - `cointegration_pairs.csv` dated before the Engle–Granger fix.

| Class | Meaning |
|---|---|
| **CANONICAL / CURRENT EVIDENCE** | Produced under the reset-point standard (§4) with full provenance; usable as evidence for what it claims |
| **LEGACY / DESCRIPTIVE ONLY** | The method has known weaknesses, but the result is a faithful **description** of the data under its stated method. It must not be cited as statistical evidence or as an edge. |
| **INVALIDATED** | A flaw that **can change the economic conclusion** (wrong P&L identity, look-ahead, a null that cannot fail, a test at its resolution floor). Do not cite. |
| **SUPERSEDED** | A corrected method or corrected data now exists; the old artifact is kept for the record |
| **UNKNOWN / UNVERIFIED** | Cannot be classified from available evidence (no artifact, no provenance) |

---

## 1. Executive summary

1. **No strategy in this repository has canonical evidence of a tradeable edge.** That covers pairs (Kalman / cointegration), DCA, cross-sectional (crypto and FX), PCA residual, contagion, feature-based, and the main single-symbol strategies. **"Edge not established" is the honest current state.**
2. **The most positive historical results are invalidated, not merely weak:**
   - the pairs "eighth/ninth design" and 7-pair portfolio conclusions (the P&L identity was the Kalman innovation; C-01/C-02/C-03);
   - the FX cross-sectional "survivor" (p = 1/21 for every configuration: about 20 permutations, the p-value floor);
   - the PCA residual IC (loadings fitted on the full sample, so look-ahead);
   - all pre-Phase-2 DCA results (same-print clock, a drift-preserving B = 100 null, FX volatility leakage).
3. **Most negative results stand as descriptive conclusions, but not as statistical proof of "no edge."** Their tests were often unable to pass (H-12: p-floor vs the BH family). Where the negative verdict rests on **economics** (costs exceed the gross effect), it is robust to the statistical weaknesses. Where it rests only on p-values, it is uninformative.
4. **There is no untouched historical holdout** (the owner's own Stage 2D lock §3.1–3.2). Every candle from 2021-09-26 to 2026-09-24 has been exposed. Every historical OOS or "holdout" result is therefore a `PSEUDO_HOLDOUT — NOT CLAIM-ELIGIBLE` development check. Genuine OOS exists only **forward** from a protocol-freeze timestamp F, which is still BLOCKED (owner).
5. **Dashboards currently display invalidated conclusions as if current** (§10). The Research Archive renders the 2026-09-27 governance log, including its "strongest, most cross-pair-consistent result" entries.
6. **The full DCA significance run is NOT READY. STOP** (§11). Its methodology is canonical in clock, fills and accounting. But no trading hypothesis or family is pre-registered, the nulls have not been size- and power-calibrated, and a realistic family (e.g. 20 symbols) makes B = 999 **structurally unable to pass**: the same defect as H-12.
7. **Phase 3** (main bot / backtester remediation) **is ready to start on approval** (§12). It is code remediation that depends on no historical result. Its outputs must be produced under the reset point.

---

## 2. Classification by research family

The reset-point questions, asked of each family:
- causal?
- look-ahead?
- execution realistic?
- costs?
- appropriate test?
- multiple testing controlled?
- genuinely OOS?
- holdout contaminated?
- reproducible?
- could the flaw change the economic conclusion?

### 2.1 Pairs: Kalman / entry-exit research (governance log 2026-08 → 09-27)

| Result | Class | Why |
|---|---|---|
| Design attempts 1–9, incl. "Eighth design: the strongest, most cross-pair-consistent result", "Ninth: 7-pair portfolio", "edge survives FIDA's real cost", calendar-year walk-forward corroboration (`model_governance_log.md` l.1608–1916, 2117) | **INVALIDATED** | **C-01:** P&L = change in the Kalman innovation, not fixed-quantity position P&L. On **independent random walks** the same formula gives +1.2 to +1.5 % per trade at a 74–81 % win rate; true P&L is −0.19 to −0.47 %. Real DODO/FIDA: +1.13 % (formula) vs **−0.26 %** (true). The flaw **reverses** the economic conclusion. Also a (1+β) magnitude error and legacy rules (H-05). |
| `research_data/permutation_test/*/permutation_verdict.csv` (31 pairs), `permutation_test_ols/*`, `edge_appears_real=True` verdicts | **INVALIDATED** | **C-03:** the null shuffles decision columns but keeps the real spread. Since z is computed from that spread, the test **rejects for any pair, related or not**. B = 100, so every value sits at the 0.0099 floor. |
| `walk_forward/walk_forward_leaderboard.csv` (09-23) | **INVALIDATED** | Built on C-01 P&L; plus M-03 (one-candle train/test overlap, no purge) and legacy rules |
| `portfolio_7pair/`, `slippage_stress/`, `dodo_fida_slippage_stress/`, the 3-pair FIDA-cost test | **INVALIDATED** | Every economic figure inherits C-01 P&L; the cost stress was applied to a phantom gross edge |
| Pairs equity curves / trade logs shown on **Pairs Performance** (`*_equity_curve.csv`, 09-23) | **INVALIDATED** | Same C-01 P&L |
| AVAX/ATOM, DOT/LINK, DODO/FIDA "REJECTED" (governance log) | **LEGACY / DESCRIPTIVE ONLY** (rejection stands) | Reached with an invalid method biased **toward** acceptance. The 09-29 corrected fixed-quantity re-run under the live configuration confirms the direction: **DODO/FIDA −0.29 %/trade, PF 0.62; MINA/ONG −0.43 %/trade, PF 0.47.** Still legacy rules and pseudo-holdout, so descriptive, not canonical. |
| 09-29 audit's random-walk calibration and `test_pairs_pnl_identity.py` | **CANONICAL / CURRENT EVIDENCE** (methodological) | Reproducible, synthetic, a known-answer null calibration. It is evidence that the historical pairs evidence is invalid, **not** evidence about any pair. |
| `frozen_candidate_registry_2026-09-20.json` (31 pairs) | **LEGACY** (already ruled, Decision Lock C4) | Pre-Stage-2D rules; 108 post-freeze candles were exposed before the 09-28 rewrite. Never Stage 2D evidence, never a holdout. |
| Kelly sizing from those win rates (C-06/H-01) | **SUPERSEDED** | Removed; fixed fraction under Stage 2D |

**Trading hypothesis and source of edge:** the proposed edge is mean reversion of a cointegrated spread, i.e. relative-value convergence. **The historical evidence never measured it.** The corrected numbers show net ≈ −cost on the pairs tested. **Status: research hypothesis; edge not established.**

### 2.2 Cointegration / Engle–Granger / ADF

| Result | Class | Why |
|---|---|---|
| `cointegration_pairs.csv` (2026-09-28), the 100-coin scan, the 19-symbol rescan, the 3 appended pairs, `*_cointegration.csv` (118) | **SUPERSEDED** | **H-03:** Dickey-Fuller p-values on *estimated* residuals are anti-conservative. The Engle–Granger/MacKinnon fix landed in `458ac14` (2026-10-01), **after** this file was written. The "OOS persistence / untouched holdout" ADF is a pseudo-holdout by owner ruling (C4 naming hazard). Selection on an anti-conservative test **can** admit spurious pairs. Must be re-run under Stage 2D zone S (currently BLOCKED). |
| Forex cointegration 0/378 (MT5), 56-pair widening, gold/silver (`forex_cointegration_pairs_*.csv`) | **LEGACY / DESCRIPTIVE ONLY** | An **anti-conservative** test found **no** candidates, so the negative is directionally robust to H-03. But the FX calendar is BLOCKED and the Stage 2D lock says *"No FX result may be read as negative evidence."* Descriptive: "no candidates under the legacy screen". |

### 2.3 Kalman research
| Result | Class | Why |
|---|---|---|
| Kalman z-score / lag features "STRONG KEEP", IC 0.52 (`*_kalman.csv`, `kalman_LAG1h`, feature verdicts) | **INVALIDATED** | **C-02:** the label `spread_{t+N} − spread_t` is built from the same innovation. On random walks IC = 0.39–0.43 **by construction**. The labels are now retired in code (`assert_label_admissible`). |
| `kalman_diagnostics.csv`, `kalman_beta_trajectory.csv`, `*_kalman_half_life.csv` | **LEGACY / DESCRIPTIVE ONLY** | Descriptive filter state. Half-lives were computed on the legacy setup; the governance log's own "foundational correction" (l.1346) shows earlier ones were mis-calibrated. Not evidence of tradeability. |

### 2.4 Permutation testing and FDR (cross-cutting)
| Usage | Class | Why |
|---|---|---|
| Pairs permutation engine (legacy rules, B = 100) | **INVALIDATED** | C-03 (above) |
| `feature_validator` block permutation (B = 100) with family-wide BH | **LEGACY / DESCRIPTIVE ONLY** | **H-12:** with m tests, BH at q needs a raw p ≤ q·k/m. With a floor of 1/101, large families **cannot** pass. Negatives are uninformative; positives sit at the floor. |
| FX cross-sectional permutation (`forex_cross_sectional_family_verdict.json`, 09-22) | **INVALIDATED** (as statistical evidence) | Every configuration reports **p = 0.047619 = 1/21**: about 20 permutations, so every value is the floor. "Passes FDR" over 4 top-k configs is an artifact of p_floor < 0.05; the test has no resolution. |
| DCA legacy permutation (B = 100, drift-preserving, close-only) | **SUPERSEDED** | Phase 2 nulls (drift-preserving and drift-removed, OHLC-aware, B ≥ 999). See §11 for what is still missing. |
| PCA IC block permutation (B = 100) | **INVALIDATED** | At the 0.0099 floor, and the underlying IC has look-ahead (§2.10) |

### 2.5 OOS / walk-forward
| Result | Class | Why |
|---|---|---|
| Every historical OOS / walk-forward / "holdout" figure, any engine | **LEGACY / DESCRIPTIVE ONLY** (at best) | The whole 2021-09 → 2026-09 history is exposed (Decision Lock §3.1): `PSEUDO_HOLDOUT — NOT CLAIM-ELIGIBLE`. Also M-03 (walk-forward boundary overlap, no purge) and M-04 (`oos_validator` p-values assume IID with overlapping labels, so they are anti-conservative). |
| Pairs walk-forward specifically | **INVALIDATED** | Plus C-01 |

### 2.6 Feature research
| Result | Class | Why |
|---|---|---|
| 20 × `*_validated_features.csv` (2026-07-25), `validated_features_institutional.csv` (09-06), Feature Intelligence "STATISTICALLY SUPPORTED / PROMISING / REJECTED" labels | **LEGACY / DESCRIPTIVE ONLY** | H-12 (p-floor vs BH); analytic IC p-values assume independence while 4 h/24 h labels overlap on hourly observations (M-04-type inflation); produced from observation files with an **all-empty ADX** (feature-calculator bug since fixed). The IC magnitudes are honest descriptions; the significance labels are not evidence. |
| `rsi` "statistically real, p_fdr 2.9e-27, economically negligible" (basis of MeanReversion REJECTED) | **LEGACY / DESCRIPTIVE ONLY** (rejection stands) | The p-value is inflated (overlap). But the **economic** conclusion (quartile spread 1e-5 to 1e-3, below costs) does not depend on it. Rejecting is the fail-safe direction. |
| `*_observations.csv` (July) | **SUPERSEDED** (pending swap) | Rebuilt files exist in the scratchpad: ADX populated; `atr_indicator` confirmed a duplicate of `atr`. Swap is Phase 6. |
| `temporal/*` (stability, decay, regime breakdown) | **LEGACY / DESCRIPTIVE ONLY** | Same inputs; full-history descriptive |
| `hypothesis_7_ic_report.csv` (contagion IC, p = 0.0, n = 43,750) | **LEGACY / DESCRIPTIVE ONLY** | Analytic IID p-values on overlapping hourly labels; IC ≈ 0.09 is descriptive |

### 2.7 Regime research
| Result | Class | Why |
|---|---|---|
| `REGIME_AWARE_RESEARCH_IMPLEMENTATION_REPORT.md`: the cross-sectional reversal is "statistically detectable, regime-**independent**, economically negative after costs in every regime" (crypto and FX) | **LEGACY / DESCRIPTIVE ONLY** (economic negative stands) | The detectability p-values inherit M-04. The **economic** negative (spread below costs) and the failed interaction test are the decision-relevant parts, and are robust in the fail-safe direction. |
| Research-lab `REGIME_DEPENDENT` (3 experiments, continuous features) | **UNKNOWN / UNVERIFIED** | Pre-reset; no methodology version; the same validator weaknesses. Re-run before any citation. |

### 2.8 Strategy scoring and main-strategy backtests
| Result | Class | Why |
|---|---|---|
| `Backtester` / `PortfolioBacktester` / `StrategyScorer` outputs (Backtesting, Strategy Research pages, dashboard stats) | **LEGACY / DESCRIPTIVE ONLY** | **M-01:** entry at the signal candle's price (same print); a gap through the stop fills at the stop (optimistic). **M-02:** drawdown at closes only. **H-11:** Sharpe/Sortino computed on R-multiples with a unit-mismatched risk-free adjustment (the Sortino denominator gave 5.0 vs a textbook 0.70). Scores are not comparable evidence. |
| MovingAverageStrategy **UNTESTED**, MeanReversionStrategy **REJECTED** (`STRATEGY_VERDICTS`) | **Gate verdicts stand** | Both keep the production gate **closed**, which is the fail-safe direction. MA has **no** research evidence at all. |

### 2.9 Support / resistance and supply / demand
| Result | Class | Why |
|---|---|---|
| Any S/R or supply/demand research | **UNKNOWN / UNVERIFIED: no artifact exists** | No engine, CSV or governance entry implements or evaluates it. The only mentions are in the DCA spec's future research list. Nothing may be cited. |

### 2.10 PCA / cross-sectional / contagion
| Result | Class | Why |
|---|---|---|
| PCA residual IC, crypto and FX (`pca_ic_validation.csv`, `forex_pca_ic_validation.csv`, 09-25; "REVIEW" labels) | **INVALIDATED** | **Look-ahead:** `run_pca_engine` estimates means, standard deviations, the correlation matrix and the loadings on the **entire** return history, then measures the residual IC on the same history. Every residual at *t* uses loadings fitted with future data. Plus a B = 100 floor. |
| Crypto cross-sectional ranking: RESEARCH_NEGATIVE (two independent runs) | **LEGACY / DESCRIPTIVE ONLY** | Weak permutation resolution makes the statistical negative uninformative. The economic negative (costs dominate) stands as description. |
| FX cross-sectional ranking, "top_k = 1 survivor / economically supported" (09-22) | **INVALIDATED** (statistical) · descriptive economics only | p = 1/21 floor (§2.4). Descriptively: mean net 1.9e-5 per period, hit rate 50.3 %, Sharpe 0.375, worst-fold drawdown 25.9 %, cumulative cost drag 32.5 %. **Not** economically meaningful evidence. The top-k sweep (1/3/5/10, only k = 1 "survives" after seeing the results) is also a forking-paths selection. |
| FX cross-sectional **reversal signal**: RESEARCH NEGATIVE (09-13 audit, 28-pair universe) | **LEGACY / DESCRIPTIVE ONLY** (negative stands) | 0.56 bps top-minus-bottom spread vs FX costs: an economic negative |
| Contagion: 2,156 symbol/feature pairs, 0 KEEP (RESEARCH_NEGATIVE) | **LEGACY / DESCRIPTIVE ONLY** | Validator H-12: the negative is **statistically uninformative** ("edge not established", not "no edge") |

### 2.11 DCA
| Result | Class | Why |
|---|---|---|
| Every DCA result before Phase 2 (incl. "BTC closest to significant", FX DCA, per-asset cycle stats) | **INVALIDATED** | Same-print decide-and-fill clock (B2); single-level crash fills mislabelled "conservative" (B1); a drift-preserving B = 100 null that cannot attribute profit to drift (B4); FX volatility scaling fitted on the full history (B5: leakage); realised and MTM mixed (B3); free shorting (B6). The clock alone changed the BTC maximum drawdown by about 2.5× across fill models, so the flaws **can change the conclusion**. |
| Phase 2 BTC descriptive run (2026-10-02; canonical clock, 3 fill models) | **LEGACY / DESCRIPTIVE ONLY** (pending provenance) | Canonical **method**, but produced from an **uncommitted** working tree, with no run ID or data fingerprint, and on exposed history. To be re-produced at the committed reset point; then it is canonical **descriptive** evidence (tail risk), never edge evidence. |

**Trading hypothesis:** a long martingale DCA earns from (a) positive drift and (b) recovery after drawdowns. Its payoff is **short-volatility / short-crash-insurance**: many small gains, rare large trapped losses. Profit consistent with drift or with a crash-risk premium is **not** an edge unless it beats both a drift-preserving null (path structure) and a fair compensation for the tail it carries. **Status: research hypothesis.**

### 2.12 Historical leaderboards and stored experiments
| Item | Class | Why |
|---|---|---|
| Walk-forward leaderboard, permutation verdict table (Robustness page) | **INVALIDATED** | §2.1 |
| Research-lab `ResearchExperiment` (46 rows, 2026-08-22 → 09-22): 2 PARTIALLY_SUPPORTED (cointegration), 12 REJECTED, 10 INCONCLUSIVE, 4 REQUIRES_REVIEW, 3 REGIME_DEPENDENT, 2 INSUFFICIENT_DATA, 13 blank | PARTIALLY_SUPPORTED (pairs): **INVALIDATED**. All others: **UNKNOWN / UNVERIFIED** | Pre-reset; the pairs ones inherit C-01/H-03; the others inherit the validator weaknesses; no methodology version. Blank verdicts are incomplete runs. |
| `order_book_depth_measurement.csv` | **LEGACY / DESCRIPTIVE ONLY** (usable as a dated measurement) | A real point-in-time order-book observation. Valid as an input with its date, not as a stationary cost |
| Live DRY_RUN pilot journal (2 rows, mode-labelled) | Operational record | Simulated fills; never account P&L (Phase 1 journal mode) |
| `scan_150_2026-09-30` | **UNKNOWN**: no results | Data download and universe selection only; the scan was halted. Nothing to cite. |

---

## 3. Descriptive vs statistical vs economic vs edge (how to read every result)

| Level | Question | Typical failure seen here |
|---|---|---|
| Descriptive | What happened in this sample under this method? | Confused with evidence (win rates, Sharpe, IC magnitudes) |
| Statistical evidence | Is it unlikely under an **appropriate** null, with controlled multiplicity and dependence? | Nulls that cannot fail (C-03); p-floors (H-12, B = 20); IID p-values on overlapping labels (M-04) |
| Economic significance | Does it survive realistic costs, financing, execution and capacity? | Effects below costs (cross-sectional, rsi); phantom gross edges stress-tested for costs (pairs) |
| Trading edge | A causal, statistically supported, economically meaningful, out-of-sample-robust return with an identified source | **Not established anywhere in this repository** |

A high win rate (pairs 74–87 %, DCA ~99 %) was, in every case examined, a property of the **exit rule or bet sizing**, not evidence of skill.

---

## 4. CANONICAL RESEARCH RESET POINT: `CRRP-2026-10-02`

Results produced **before** this point are **not** current evidence by default. They keep the classifications above.

| Element | Definition |
|---|---|
| **Identifier** | `CRRP-2026-10-02` |
| **Code version** | Base `565e55d`, plus the Phase 1/2 working tree. **PENDING COMMIT:** no result can carry code provenance until the owner commits. The reset point's code hash is the hash of that commit. |
| **Methodology versions** | Pairs: `STAGE2D-REVB-3`, protocol hash `7dbaa8fe8bab068708565852950589fd8d546c53d4ee7d68b75182f8fe455a6c` (`STAGE2D_CANONICAL_PROTOCOL.md` sha256 prefix `e667ce02cf18345d`, `STAGE2D_DECISION_LOCK.md` `9aa5bf5357cdf49c`). DCA: `DCA_RESEARCH_PROTOCOL_2026-10-02.md` (`312039bb19f74c1a`), strategy version `2026-10-02.2-canonical-clock-<fill_model>`. **All other engines** (feature validator, walk-forward, OOS validator, PCA, cross-sectional, contagion, regime, main backtester) have **no canonical methodology yet**; anything they produce is LEGACY until their open findings (H-11, H-12, M-01–M-04, PCA look-ahead) are fixed and versioned. |
| **Data lineage** | `research_data/_governance/data_manifest_CRRP-2026-10-02.json`: 446 price files (`data/*.csv`, `data/forex/*.csv`) with SHA-256, rows, first/last timestamp, mtime. Last candles: crypto to 2026-09-24/27; some files to 2026-09-18. **Known heterogeneity:** 233 files use epoch timestamps and 213 ISO. Every result must cite the fingerprints of the files it read. |
| **Timing rules** | Decisions only on **closed** candles, from information ≤ decision time; execution at the next available price per each protocol (Stage 2D C1–C3; DCA D1–D6); gaps never invented; elapsed-time rules in wall-clock. |
| **Execution / costs** | Declared `FEE_RATE` / `SLIPPAGE_RATE` / venue table; liquidation-value marks for risk; borrow/financing labelled KNOWN / UNAVAILABLE / ASSUMED_ZERO / NOT_APPLICABLE (never a silent zero); results with UNAVAILABLE costs carry `cost_complete=False`. |
| **Statistical standards** | **(1)** A null must be shown appropriate **before** use: size ≈ α on synthetic no-edge data and power on a synthetic true edge (calibration test in the suite). **(2)** B ≥ max(999, ⌈m/(q·k)⌉ − 1) for a pre-declared family of m tests at FDR q with k intended discoveries, or Bonferroni equivalently; report p_floor = 1/(B+1). **(3)** Inference on serially dependent or overlapping observations uses block bootstrap or HAC, never IID p-values. **(4)** Both statistical and economic criteria are reported; neither alone is "edge". |
| **Pre-registration** | Before any significance run: the trading hypothesis and its source of edge, the family (symbols × configs × nulls × block sizes), statistics, decision rule, B, seeds and data fingerprints, recorded in a registry entry **before** results are seen. |
| **OOS / holdout** | All history ≤ 2026-09-24 (and anything fetched since) is **exposed**: `PSEUDO_HOLDOUT — NOT CLAIM-ELIGIBLE`. Claim-eligible OOS only for candles with open time ≥ **F** (the Stage 2D protocol-freeze timestamp: BLOCKED, owner), evaluated once, with no refit. |
| **Provenance (every result)** | methodology version, protocol hash, code commit, data fingerprint(s), run ID, seed, family ID and size, role (development / pseudo-holdout / forward-holdout), costs and financing status, exclusions, missing-data treatment |
| **Governance log** | `model_governance_log.md` is **preserved unchanged**. Supersession is to be recorded by an **appended** versioned entry pointing to this report (proposed, §13; not written without approval). |

---

## 5. Current methodology standard (one line per engine)

| Engine | Status at CRRP | Before any new result counts |
|---|---|---|
| Pairs (Stage 2D) | Canonical lifecycle, P&L and provenance | Steps 12–20; zones S/V/WF/F (owner); canonical permutation (Step 17) |
| DCA | Canonical clock, fills, accounting, nulls | §11 prerequisites |
| Feature validator | Legacy | H-12 family-sized B or a two-stage design; dependence-aware p-values |
| Walk-forward / OOS validator | Legacy | M-03 purge and exclusive boundary; M-04 HAC/block inference |
| PCA | **Invalid** (look-ahead) | Rolling / expanding PCA fit on information ≤ t only |
| Cross-sectional / contagion / regime | Legacy | B and family per §4; dependence-aware inference |
| Main backtester / scorer | Legacy | Phase 3: H-07, M-01, M-02, H-11 |

---

## 6. Results requiring rerun (priority order; none run now)
1. **Pairs candidate selection:** Engle–Granger/MacKinnon under Stage 2D zone S (blocked on the owner's zones).
2. **Pairs canonical permutation** (Step 17), with a null that destroys the cross-asset relationship and is calibrated on random-walk pairs.
3. **DCA descriptive baseline** at the committed reset point (cheap: seconds per symbol), then a pre-registered significance run (§11).
4. **Feature research** on the rebuilt observation files (Phase 6 swap) **after** H-12 / M-04 are fixed. Until then the July labels stay as they are, marked legacy.
5. **PCA** with a causal (rolling) fit.
6. **Main-strategy backtests** after Phase 3.

## 7. Results that remain usable (for what they claim)
- The **methodological evidence** that old pairs P&L / IC / permutation was invalid (C-01–C-03 calibration probes and tests).
- The **corrected** 09-29 pairs numbers (negative) as a descriptive sanity check.
- **Economic negatives** where costs exceed the gross effect: cross-sectional reversal (crypto, FX), rsi; also the regime interaction test failing.
- The order-book depth measurement (dated).
- Phase 1/2 verification results (software and accounting correctness).

## 8. Descriptive only
Every **LEGACY / DESCRIPTIVE ONLY** row above: feature IC magnitudes, Kalman diagnostics, FX cointegration screens, regime breakdowns, backtester outputs, DCA Phase 2 BTC run (until re-produced with provenance), research-lab experiments with pre-reset verdicts.

## 9. Invalidated
- Pairs design attempts 1–9 and every figure stacked on them (permutation verdicts, walk-forward leaderboard, 7-pair portfolio, slippage/cost stresses, equity curves).
- Kalman spread-label IC / KEEP.
- PCA residual IC.
- FX cross-sectional "survivor".
- All pre-Phase-2 DCA results.
- Research-lab PARTIALLY_SUPPORTED pairs experiments.

---

## 10. Dashboard and leaderboard implications (audit; no page changed)

| Page | What it shows today | Risk | Required control (proposed) |
|---|---|---|---|
| **Research → Research Archive / Rejected Research** | Renders `model_governance_log.md` sections, incl. "strongest … result", "7-pair portfolio", "edge survives" | **High:** invalidated conclusions presented as current | A per-section classification badge from a governance registry, plus a page banner: "Pre-CRRP-2026-10-02 legacy log; see the reassessment report" |
| **Research → Robustness Analysis** | `permutation_verdict.csv` "STATISTICALLY SUPPORTED/REJECTED" and the walk-forward leaderboard | **High** | Mark INVALIDATED; hide the verdict labels behind "legacy (C-01/C-03)" |
| **Research → Pairs Performance** | Pre-fix equity curves, trade logs, verdicts | **High** | INVALIDATED banner; no verdict colouring |
| **Research → Research Lab (overview)** | Feature labels, cointegration table (DF p-values), backtest panel | Medium | LEGACY badges per panel |
| **Research → Feature Intelligence** | "STATISTICALLY SUPPORTED / PROMISING" labels | Medium | LEGACY label: "significance not evidence (H-12, overlap)". The correlation page is descriptive and already upgraded. |
| **Research → Strategy Research / Backtesting** | Live backtests (M-01/M-02/H-11 metrics) | Medium | "Descriptive, legacy backtester" banner until Phase 3 |
| **Research → Strategy Pipeline** | MA UNTESTED, MR REJECTED | Low (fail-safe) | None |
| **Research Lab → History** | 46 pre-reset experiments with verdicts | Medium | "pre-CRRP" badge; PARTIALLY_SUPPORTED pairs marked INVALIDATED |
| **Forex dashboard** (cross-sectional section) | Dispersion / extremes (descriptive) | Low | None. The family verdict JSON is not displayed as a strategy. |
| **Pairs Pilot / Execution Monitor** | DRY_RUN journal, mode-separated (Phase 1) | Low | Done |

**Smallest robust control (proposed, not implemented):**
- a single machine-readable registry, `research_data/_governance/evidence_registry_CRRP-2026-10-02.json`, mapping artifact → class, reason and report section;
- one template partial that renders the badge;
- every Research page looks up its artifacts there.

An unregistered artifact displays **UNKNOWN / UNVERIFIED**. It is never shown as current by default.

---

## 11. DCA significance-run readiness: **NOT READY. STOP.**

**Canonical now:**
- clock and fills (D1–D6, three labelled models);
- MTM accounting;
- censoring;
- financing labels;
- leakage-free volatility fitting;
- OHLC-aware drift-preserving and drift-removed nulls;
- B default 999;
- block-size sensitivity;
- in-suite Bonferroni.

**Missing (each is blocking):**

| # | Gap | Why it blocks |
|---|---|---|
| 1 | **No pre-registered hypothesis and family** | The decision rule, symbols, fill models, nulls, block sizes and statistics must be fixed before results are seen. Choosing them afterwards is forking paths. |
| 2 | **Family vs resolution** | 20 symbols × 2 fill models × 2 nulls × 3 block sizes = 240 tests. Bonferroni at 0.05 needs p < 2.1e-4, but B = 999 gives a floor of 1.0e-3. **The run would be structurally unable to pass:** H-12 again. The required B ≈ 4,800 means about 22 h per symbol-model (4,800 × 6 tests × 2.7 s ≈ 77,800 s). Design a smaller family, or a two-stage design (screen, then confirm), first. |
| 3 | **Nulls not calibrated** | No test yet shows that the DCA nulls reject at ≈ α on synthetic no-edge paths (driftless random walk; random walk with drift under the drift-preserving null) and have power on a synthetic path with planted mean reversion. Without it, a rejection cannot be interpreted (the lesson of C-03). |
| 4 | **Exposed data only** | Every candle is pseudo-holdout, so any result is **development evidence, not claim-eligible**. That must be stated in the registration. |
| 5 | **No committed code / provenance** | Results would carry no code hash. |
| 6 | **Economic criterion undefined** | A pass also needs a pre-declared economic bar (e.g. MTM P&L net of costs relative to the maximum-drawdown capital at risk, compared with a drift / buy-and-hold benchmark of equal capital). Otherwise a drift-explained profit could be read as edge. |

**Recommended sequence, after approval:**
1. Commit.
2. Null calibration tests: size and power, cheap on synthetic data.
3. Register the hypothesis, family, B and economic bar.
4. Run on a **small** pre-declared family.
5. Report as pseudo-holdout development evidence.

---

## 12. Phase 3 readiness: **READY on approval** (conditions)

**Phase 3 scope:** H-07 (forming candle), M-01 (fills), M-02 (drawdown), H-11 (Sharpe/Sortino).

Phase 3 depends on **no** historical result: it fixes the main bot and backtester code. Conditions:
1. The live bot stays `DRY_RUN = True`. H-07 is a live-path change, tested with recorded and synthetic candles, no exchange.
2. All Phase 3 backtest outputs are produced and labelled under `CRRP-2026-10-02`.
3. Old backtester numbers are kept and marked LEGACY, never silently replaced.
4. The commit question (§4) is answered first, so that Phase 3 outputs can carry provenance.

---

## 13. Owner decisions requested (nothing below is done yet)
1. **Commit** the Phase 1/2 working tree, to fix the reset point's code hash.
2. **Approve appending** a supersession entry to `model_governance_log.md`, pointing to this report. The existing text stays unchanged.
3. **Approve the evidence registry and dashboard badges** (§10).
4. **DCA:** approve the §11 sequence: calibration → registration → small family. Or defer it.
5. **Start Phase 3** under the §12 conditions.
6. **Stage 2D:** zones S/V/WF/F and the forward holdout start F (unchanged; still BLOCKED).

---

## 14. Five-area assessment of the research programme as of the reset point

| Area | Assessment |
|---|---|
| Software | Engines run and are increasingly tested; Phase 1/2 verified; legacy engines unchanged |
| Mathematics | Pairs P&L identity fixed (C-01); DCA accounting fixed; **PCA look-ahead** and Sharpe/Sortino (H-11) open |
| Statistics | The weakest area historically: nulls that cannot fail, p-floors vs families, IID inference on dependent data. The standard is now defined (§4) and implemented only for DCA |
| Quantitative trading | **No established edge.** The most credible conclusions are economic **negatives** (costs exceed effects). Positive historical results traced to measurement artifacts or untestable statistics |
| Research governance | No untouched historical holdout; pre-registration absent before today; provenance partial. Reset point defined; dashboards not yet aligned |

**Bottom line:** the honest result of the research programme to date is **"edge not established"**. That is an acceptable outcome. It is far better than the alternative the historical record presented, which was apparent edges produced by the research process itself.
