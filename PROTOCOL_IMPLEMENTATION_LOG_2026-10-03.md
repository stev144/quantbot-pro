# Protocol Implementation Log: Decision Sheet D-1 to D-11

**Authority:** `DECISION_SHEET_D1-D11_2026-10-03.md`, all rows approved as written by the owner on 2026-10-03.

**Specific answers recorded with the approval:**
- forward holdout F = 2026-10-04 00:00 UTC;
- borrow row = perpetual funding rates.

**Order:** D-8 → D-1 → D-2/D-10 → D-3 → D-4 → borrow/funding → D-7/D-11 → D-6, D-5.

**Rules for every step:**
- each step is reassessed on all five gates independently;
- no historical result is deleted or reclassified;
- no research, live trading or holdout evaluation is run.

---

## D-8: Research zones and sealed forward holdout. Status: IMPLEMENTED AND VERIFIED

### What changed

| File | Change |
|---|---|
| `bot/quant_core/research_zones.py` (new) | Frozen `ResearchZones`, `QBP-ZONES-1`, with `zones_hash()`:<br>• `classify` / `classify_index` (zones PRE_INIT_UNUSED, INIT, SELECTION, EMBARGO, VALIDATION, UNASSIGNED_EXPOSED, FORWARD_HOLDOUT_SEALED);<br>• `claim_eligible`;<br>• `assert_no_sealed_data`;<br>• `split_sealed`;<br>• `to_utc` (epoch ms / s aware);<br>• `write_sealed_rows` (append-merge, never overwrites a sealed candle);<br>• `seal_before_write`;<br>• `open_holdout_evaluation`: refuses before due, refuses a second evaluation, records a DEFERRED event (+90 days) when trades < 30, records before any read. Uses the ledger `bot/research/governance/forward_holdout_ledger.json`.<br>Its `__post_init__` refuses: out-of-order zones, an embargo ≠ 504, an evaluation date ≠ F + 182 days, and off-grid boundaries |
| `bot/quant_core/protocol.py` | `METHODOLOGY_VERSION` `STAGE2D-REVB-3` → **`STAGE2D-REVB-4`**; new field `selection_start_utc = ZONES.selection_start` (None = labelled test/development protocol with a different hash); `require_selection_start()`; validation of the field |
| `bot/quant_core/seed.py` | `causal_init`: under a canonical protocol the seed candle **is** the S start, and θ₀ = OLS on the L candles immediately before it. It refuses when the S start is absent or fewer than L candles precede it. `validate_seed`: refuses a seed not at the S start |
| `bot/research/kalman_filter_engine.py` | `run_canonical` refuses sealed candles (`assert_no_sealed_data`) |
| `bot/fetch_all_symbols.py`, `bot/forex_data_fetcher.py`, `bot/mt5_data_fetcher.py`, `bot/fetch_derivatives_data.py` | `seal_before_write` before each raw-data CSV write: rows ≥ F go to `data/<dir>/_sealed_holdout_F2026-10-04/`, never to the research files |
| `bot/research/parity_datasets.py` | The synthetic dataset starts at the canonical init-window start, so tests exercise the **real** seed placement (values unchanged) |
| Governance | `research_data/model_governance_log.md` entry (append-only, CRLF, prefix verified intact); `STAGE2D_DECISION_LOCK.md` supersession **S-2** (original text kept); `STAGE2D_CANONICAL_PROTOCOL.md` version header; decision sheet sign-off |

### Tests

**New:**
- `test_research_zones` (14):
  - exact signed boundaries;
  - zones ↔ protocol agreement;
  - every boundary candle classified;
  - embargo = 504 with no overlap;
  - inconsistent definitions refused;
  - hash changes on any boundary change;
  - seal refuses ≥ F and passes < F;
  - `run_canonical` refuses sealed data;
  - the sealed store never overwrites;
  - epoch ms / s sealing;
  - `seal_before_write`;
  - ledger: not due, deferral then evaluate-once, foreign ledger refused.
- `test_canonical_seed` (+4):
  - pre-init candles cannot change the seed;
  - refusal for history that doesn't cover the init window;
  - refusal when the S start is absent;
  - a seed record moved off the S start is rejected.

**Existing tests retargeted (justified, no assertion weakened):**
- **`test_canonical_seed`:** the seed position moves from "candle L" to the S-start position; init-window mutations moved inside the new init window.
- **`test_stage2d_provenance`:**
  - the methodology assertion moves REVB-3 → REVB-4 (deliberate bump), plus a new assertion on the signed S start;
  - the "wrong L" mutation now uses a 504-candle window that still ends before the seed. The old 2023 date fell after the window end and tripped the causality check instead of the L check; it was still rejected, but by the wrong check.

**Mutation check:** disabling the D-8 branch of `causal_init` makes **11 tests fail**. The file was restored byte-identical.

**Defects caught by the new tests during implementation (fixed before acceptance):**
1. The sealed-store dedup compared CSV strings with Timestamps, so a re-fetch would have duplicated sealed candles.
2. Epoch-ms timestamps (funding/OI) would have parsed as 1970, so nothing would have been sealed.

**Regression, all under the offline network guard:**

| Run | Tests | Result |
|---|---|---|
| 21 modules using the protocol, seed, synthetic datasets or the four fetchers | 280 | 270 pass, 10 failures. **All 10 are in the clean-HEAD baseline failure list** (network-only or data-freshness); **0 new** |
| Pairs / Stage 2D / seed / zones / PCA / health set after all fixes | 243 | **243 pass** |

### Five-gate check (verified in this step)

| Gate | Result | Evidence |
|---|---|---|
| G1 Software | **PASS** | Unit, boundary and refusal tests; a mutation is caught by 11 tests; two defects found and fixed; line endings preserved (CRLF files verified); regression shows 0 new failures |
| G2 Mathematics | **PASS** | Boundary arithmetic asserted: init = S − 1000 h; V0 = S1 + 1 h + 504 h; E = F + 182 d; 504 embargo candles counted exactly; the θ₀ known-answer test (independent `lstsq` on the init window) passes at the new position |
| G3 Statistics | **PASS (scope)** | Zones separate selection from validation with an embargo of one z-window, so no rolling window spans S and V. Every pre-F zone is labelled not claim-eligible. The holdout is single-use and ledgered. No test statistic is introduced by D-8 |
| G4 Quant / trading | **PASS** | No candle ≥ F can enter research files or the canonical Kalman path. Seed placement is causal (θ₀ uses only pre-S candles; future and pre-init perturbations can't change it). Live behaviour is unchanged (no canonical seed existed; entries were already refused) |
| G5 Research integrity | **PASS** | Single source with a version and hash; methodology bump so a REVB-3 seed can't validate; append-only governance and supersession records; owner sign-off recorded; evaluate-once ledger; nothing deleted |

### Open questions for the owner (not decided here)

1. **Dashboards and live views are not sealed.** The seal covers research data files and the canonical evidence path. Dashboard backtests (`data_fetcher.get_klines` over live REST), Research Lab tool runs and the legacy dry-run pairs pilot can still **display** performance on candles ≥ F.
   - Viewing post-F strategy performance is a form of holdout exposure.
   - **Decision needed:** cap these displays at F, or accept that they show only non-canonical, legacy-rule output.
   - **Recommendation:** cap any display of a *canonical-candidate* strategy at F; the legacy displays may continue, labelled.
2. **Short-history pairs are ineligible.** A pair whose common history doesn't cover 2021-09-26 08:00 → 2021-11-06 23:00 cannot be seeded canonically under D-8 + Design A. FIDA (data from 2021-09-30) is one. This follows mechanically from the approved rules; confirm it is intended.
3. **The dry-run pilot is not the holdout evaluation.** It keeps running on legacy rules after F. Its logs must never be used as forward-holdout evidence; only a ledgered evaluation of a pre-registered, frozen REVB-4 protocol counts.

---

## D-1: Execution delay d = 1, fill at the next bar's open. Status: IMPLEMENTED AND VERIFIED

**Contract** (`bot/quant_core/timing.py`; the contract itself is unchanged by the PCA work):
- `RESEARCH_LABEL_CONTRACT = TimingContract(1, "open")`;
- `SENSITIVITY_CONTRACT_D2 = TimingContract(2, "open")`, a sensitivity check only;
- `LABEL_TIMING_BASIS`;
- `research_forward_return(df, h)` = open(t+1+h) / open(t+1) − 1, which **raises when `open` is missing** (no fallback to close).

### What changed

| Engine | Old label | New label |
|---|---|---|
| `feature_calculator._calculate_forward_return` | close(t+N)/close(t) − 1, inside a `try/except` that returned NaN on any error | D-1 contract; the silent `except` removed. The outer OHLCV guard already requires `open` |
| `derivatives_engine._add_forward_returns` | close(t+N)/close(t) − 1 | D-1 contract |
| `contagion_engine._calculate_forward_returns` | Σ close returns T+1…T+N | D-1 contract; docstring corrected |
| `cross_section_engine._calculate_forward_returns` | **Winsorised** close return of bar t+1 (`shift(-1)`) | D-1 contract on each symbol's **raw** opens. Raises without the OHLCV frame. Raw, not winsorised: winsorising a P&L label understates its tails |
| `pca_engine` (**owner choice: option 1**) | Close residuals summed from the signal close | `compute_open_labels_causal`. The single causal factor model, fitted on **close** returns before each refit point, is **frozen at the decision bar t** and applied to **open-to-open** log returns. label_h[t] = Σ_{j=t+2}^{t+1+h} residual_open[j], i.e. the factor residual of ln(open_{t+1+h} / open_{t+1}). The shared fit loop was extracted to `_causal_fits` (signal residuals unchanged; the existing tests guard this). The old `add_forward_returns` is kept, marked LEGACY |

**Column names are unchanged** (`forward_return_*`), so consumer contracts hold. The timing basis is recorded here and in the governance log; no metadata column was added, because validators treat extra numeric columns as features.

### Tests

**New:**
- **`test_d1_research_labels` (8):**
  - the contract equals the signed decision;
  - known-answer and d = 2 values;
  - no fallback to close;
  - for each of the four engines: known answer, a perturbed signal close leaves the label unchanged, and a missing `open` raises.
- **`test_pca_causal` (+5):**
  - known answer, including the frozen decision fit across a refit inside the hold;
  - close returns and pre-fill opens cannot change the label;
  - holding-period opens do change it (positive control);
  - NaN without an exit open or a fit;
  - misaligned inputs refused.

**Existing tests updated (justified; each still tests its original intent):**
- **`test_feature_calculator.ForwardReturnTest`:** a close-only frame now refuses. The test gets opens that differ from closes, asserts the open-based value, and adds a refusal assertion.
- **`test_contagion_engine…uses_future_candles_not_past`:** opens added; asserts open(T+3)/open(T+1) − 1 and still only future candles.
- **`test_derivatives_engine.ForwardReturnCorrectnessTest`:** expected value changed to open[103]/open[101] − 1, with distinct opens.
- **`test_derivatives_engine.DerivativesICReporterTest`:** its constant opens gave an all-zero label (IC undefined, NaN), so the opens now vary. The `|IC| ≤ 1` assertion is unchanged.

**Mutation checks** (each file restored byte-identical afterwards):

| Mutation | Result |
|---|---|
| Contract fills at the close | 7 of 8 D-1 tests fail |
| PCA label from close returns | 3 PCA tests fail |
| PCA window shifted to include the pre-fill bar | 2 PCA tests fail |

**Regression (guarded):**

| Run | Tests | Result |
|---|---|---|
| 23 label-consumer modules | 367 | 4 new failures, all the tests listed above (fixed); every other failure is in the clean-HEAD baseline list |
| After the updates | 66 | Only the 2 baseline network-only derivatives tests fail |
| PCA | 10 | **10 pass** |

**End-to-end PCA smoke** on 3 real symbols, with `save_outputs` stubbed so `research_data/` is untouched:
- labels aligned to the signal index (42,291 rows);
- 42,286 non-null 4h labels (the last 5 rows have no exit open).

### Five-gate check

| Gate | Result | Evidence |
|---|---|---|
| G1 Software | **PASS** | Fail-closed on a missing `open`; the silent `except` removed; mutations caught; regression shows 0 unexplained failures; CRLF preserved |
| G2 Mathematics | **PASS** | Known answers per engine; PCA label = hand-derived frozen-fit residual sum (atol 1e-15); window boundaries verified by a positive control and a shift mutation |
| G3 Statistics | **PASS (scope)** | Labels no longer contain bid-ask-bounce zero-latency inflation (batch-1 demonstration: same-close IC 0.157 vs next-open 0.014). Dependence-aware inference is D-2 (next) |
| G4 Quant / trading | **PASS** | Every research label now starts at an executable price, open(t+1), after the signal close. PCA hedge ratios are frozen at the decision. The cross-sectional label is raw (what a trade earns) |
| G5 Research integrity | **PASS** | One contract, one source; the owner's PCA choice recorded; old labels marked LEGACY (not deleted); governance-log entry; past results unchanged in classification (still LEGACY / INVALIDATED; new labels mean any rerun is a **new** experiment) |

### Flagged (not decided here)

- **Same-close spread labels outside D-1's list:** `cointegration_engine` (l. 1449) and `kalman_filter_engine` (l. 1738) still build same-close spread labels. These are legacy IC diagnostics; pairs trading uses the lifecycle's own d = 1 rule. Not in D-1's list of engines; owner to confirm whether D-1 extends to them.
- **Labels are row-based:** a data gap stretches the wall-clock horizon. This is pre-existing and unchanged; D-8's gap policy applies at selection.


---

## D-2 / D-10: Block permutation is the confirmatory test; block = max(4h, 24). Status: IMPLEMENTED AND VERIFIED

**Single source:** `bot/quant_core/inference.py`.
- **Rules:** `INFERENCE_METHOD_VERSION = QBP-INF-D2D10-1`, `CONFIRMATORY_B = 999` (D-3), `d10_block_length` = max(4h, 24), `d10_hac_lag` = 2h, `d10_sensitivity_blocks` = (2h, 8h), reported only.
- **`confirmatory_ic_test`:**
  - decision `p_value` = block permutation;
  - `p_hac` and `p_iid` are descriptive only;
  - fewer than 2 blocks → UNTESTABLE, never a substitute p;
  - `seed` required and recorded.
- **Helpers:** `horizon_from_label` (fails closed, never guesses h) and `stable_seed`.

**Calibration of the approved rule** (synthetic, known truth; `research_data/_governance/inference_calibration_D10_2026-10-03.json`; R = 300, SE ±1.26 %):

| Measure | Result |
|---|---|
| Size, all 9 scenarios (h ∈ {1, 4, 24} × φ ∈ {0, 0.9, 0.99}) | **3.3–6.3 %** at nominal 5 % |
| Power, IC 0.05 at h = 4 | 0.39 |
| Power, IC 0.05 at h = 24 | 0.525 |
| Power, IC 0.03 | 0.095 (small effects are hard to detect at n = 2,000; a property of the data) |

**Interaction test** (Freedman–Lane block permutation of restricted-model residuals; `inference_calibration_D2_interaction_2026-10-03.json`; R = 300 for size, 200 for power):

| Horizon | Freedman–Lane block size | IID ANOVA size | Freedman–Lane power (γ = 0.08) |
|---|---|---|---|
| h = 1 | 4.0 % | 4.0 % | 0.32 |
| h = 4 | 6.0 % | **27.3 %** | 0.28 |
| h = 24 | 4.7 % | **56.7 %** | 0.11 |

- The higher apparent "power" of the IID ANOVA is mostly false positives.
- Freedman–Lane is the standard permutation test of a partial effect; using it applies D-2's method to the interaction question. Recorded here as an implementation choice.

**Wired** (the decision p is now the block permutation; the IID p is kept as a descriptive column):
- `regime_conditional_ic`: per-cell `ic_pvalue_block`, with BH on it; the interaction test is Freedman–Lane.
- `regime_conditional_status`: explanation updated.
- `contagion_engine` and `derivatives_engine` IC reporters: `pvalue` = block p, plus `pvalue_iid`, block, B, seed.
- `feature_decay_analyzer`: per-year and per-regime grades; UNTESTABLE when the test can't run.
- `feature_stability_analyzer`: verdict; UNTESTABLE when the test can't run.
- `feature_validator`: block = max(4h, 24), B = 999 (it already decided on block permutation; its IID `ic_pvalue` was computed but unused).
- `research_lab/tools/conditional_tools`: block = max(4h, 24), B = 999.
- `research_lab/tools/regime_tools`: passes the horizon explicitly.

**Not converted, documented:**
- `kalman_filter_engine`'s "best signal" log line runs on the Stage-2D-**retired** `spread_forward_4h` label (legacy diagnostic, not a decision).
- `oos_validator`'s per-fold p feeds no decision.

**Tests:**
- **`test_d2_inference` (10):**
  - rules;
  - fail-closed horizon;
  - decision = block, IID/HAC descriptive, reproducible;
  - UNTESTABLE gets no p;
  - **adversarial:** a true-null sample with IID p ≈ 1e-5 but block p 0.345;
  - BH runs on block p (an IID-significant OVERALL row does *not* pass);
  - unparseable horizon refused;
  - interaction basis and reproducibility;
  - validator block 24 / B = 999;
  - lab B = 999.
- **Existing regime tests (41)** now pass the fixtures' true horizon (`horizon_candles=1`; one-period, non-overlapping labels). They include the planted-interaction detection and its negative control under Freedman–Lane.
- **Mutation:** BH on the IID p → caught.
- **Defects found and fixed during the step:**
  - a regex written with a literal backspace character;
  - the horizon parse inside the interaction `try` (it reported MODEL_FAILED instead of raising);
  - one unrequested filter removal that I reverted to the original behaviour.

| Gate | Result |
|---|---|
| G1 Software | PASS |
| G2 Mathematics | PASS: block = max(4h, 24) asserted; Freedman–Lane F from QR projections |
| G3 Statistics | **PASS**: the approved test is calibrated (size within ±1 SE of 5 % everywhere); IID removed from every decision path |
| G4 Quant / trading | N/A |
| G5 Research integrity | PASS: method version, block, B and seed recorded per result; calibration artifacts written |

---

## D-3: Families, pre-registration, Holm, two-stage scans. Status: IMPLEMENTED AND VERIFIED

**`bot/quant_core/hypothesis_registry.py`** (`QBP-HYP-D3-1`), with an append-only event file `bot/research/governance/hypothesis_registry.json` (created on first registration):
- **Family:** one per program per quarter; at most 5 primary hypotheses, each with a written economic rationale.
- **Endpoint:** NET_MEAN_RETURN_PER_TRADE or IC.
- **Pre-registration:** registration must precede testing; a family is **CLOSED** after `confirm` and is tested once.
- **`confirm`:**
  - results must cover exactly the registered family (no dropping);
  - B = 999 enforced;
  - data must be **zone V only** (zone S or sealed-holdout timestamps are refused);
  - Holm at FWER 0.05.
- **`exploratory_screen`:** BH at q = 0.10, **zone S only**, labelled `EXPLORATORY — NOT CLAIMABLE`, nominates at most 5.
- **Duplicates:** `atr` ≡ `atr_indicator` count once (F-21).

**Tests:** `test_d3_hypothesis_registry` (8): Holm matches statsmodels; every refusal path. **Mutation:** removing Holm's multiplicity factor is caught by 2 tests.

| Gate | Result |
|---|---|
| G1 | PASS |
| G2 | PASS (Holm known answers) |
| G3 | PASS (family-wise control, single test per family, two-stage scans) |
| G4 | N/A |
| G5 | PASS (append-only; registration-before-testing enforced; zone-linked) |

No family has been registered; nothing has been tested.

---

## D-4: Step-17 canonical pairs null. Status: IMPLEMENTED; CALIBRATION REQUIRED BEFORE USE (BLOCKED on compute decision)

**`bot/research/pairs_step17_null.py`** (`QBP-STEP17-D4-1`):
- `shift_leg_b`: circular shift of leg B's log-returns, path rebuilt from the same start price; leg A untouched.
- `draw_shifts`: shift s ∈ [720, n − 1 − 720].
- `step17_null_test`: one-sided p = (1 + #{T* ≥ T}) / (B + 1), B = 999; any non-finite replicate → UNTESTABLE.
- `canonical_net_mean_return_per_trade`: the D-3 endpoint from the **full canonical pipeline** per replicate.
- `stationary_block_bootstrap` (robustness): **mean block length has no default**. The sheet does not set it; flagged for the owner.
- **Gate:** `require_calibrated` refuses canonical use unless the artifact shows size within 3–7 % with R ≥ 500, plus a power result.

**Tests:** `test_d4_step17_null` (7):
- the shift preserves B's returns and start price;
- it destroys the cross-asset relationship (return correlation > 0.5 → < 0.05);
- shift bounds;
- the robustness parameter is required;
- exact p arithmetic and reproducibility;
- **size sanity of the shift mechanics** on 200 unrelated random-walk pairs with a cheap statistic (within 1–10 %);
- every calibration-gate refusal;
- a smoke run of the real pipeline statistic.

**Mutation:** a no-op shift is caught by 3 tests.

**Compute finding (stop-and-report):** one full-pipeline replicate takes ≈ 3.2 s at 6,000 candles (≈ 25 s at 43,800).

| Run | Estimate on this 8-core machine |
|---|---|
| Required size calibration, R = 500 at B = 99 (enough for a size check) | ≈ 44 h single-threaded, ≈ 6 h on 8 cores |
| Same at B = 999 | ≈ 18 days single-threaded |
| One real pair at B = 999 | ≈ 7 h |

**DECISION REQUIRED (owner):** how to run the calibration:
- (a) an overnight 8-process job (B = 99);
- (b) cloud compute;
- (c) first engineer a faster in-memory pipeline, then calibrate.

Until then **the null is not usable** (fail-closed gate).

| Gate | Result |
|---|---|
| G1 | PASS |
| G2 | PASS (path reconstruction, p formula) |
| G3 | **BLOCKED** (calibration not run) |
| G4 | PASS (full pipeline incl. costs per replicate) |
| G5 | PASS (version, seed, gate) |

---

## Borrow row: Short leg via USDT-M perpetuals, financing = recorded funding. Status: IMPLEMENTED AND VERIFIED (research); live execution NOT changed

**`bot/quant_core/funding.py`** (`QBP-FUND-1`):
- **Cost:** the short **receives** +rate × notional per settlement, so cost = −Σ rate × |qty| × last close at or before the settlement.
- **Coverage, fail closed:**
  - the series must span the window;
  - no gap > 8 h + 60 s jitter. The jitter allowance was found from real data: the BTC history's max gap is 8 h 0 min 0.047 s, so an exact 8-hour limit would have rejected all real history;
  - 4-hour cadences are accepted.
- **No imputation.**
- **`load_funding_series`** reads the recorded files written by `bot/fetch_derivatives_data.py` (119 symbols on disk; BTC 2020-01-01 → 2026-09-19).

**`EntryExitEngine`:**
- new `funding_rates_a/b`; both legs required; mutually exclusive with constant rates;
- `financing_mode` PERP_FUNDING / BORROW_RATE_ASSUMED / NONE;
- `_apply_funding` folds funding into the realised slice (`borrow_cost`, `net_pnl`, `roc`), so trade totals and MTM equity stay one number;
- any trade with a gap → `BORROW_UNAVAILABLE` and not cost-complete;
- `borrow_status` ∈ {FUNDING_KNOWN, BORROW_UNAVAILABLE, BORROW_RATE_ASSUMED, ASSUMED_ZERO_BORROW};
- **CANONICAL only with FUNDING_KNOWN.**

**Consequence (from the signed row):** constant user-supplied rates are an assumption, not a recorded source, so they no longer make an output CANONICAL.

**Tests:**
- **`test_perp_funding` (10):**
  - known answer and sign;
  - 4-hour cadence;
  - coverage refusals;
  - zero-settlement window;
  - NaN refusal and epoch-ms index;
  - ms jitter accepted but a missing settlement refused;
  - an engine run with recorded funding is CANONICAL and P&L moves by exactly the funding;
  - the full cost decomposition holds per trade;
  - constant rates → BORROW_RATE_ASSUMED;
  - a funding gap → BORROW_UNAVAILABLE with gaps listed;
  - mixed or one-sided financing refused.
- **Tolerances** were derived from the logged rounding (net at 2 dp, borrow at 6 dp), not loosened. My first versions wrongly assumed exact equality and "funding on every trade"; holds without a settlement correctly have 0.
- **Updated (justified by the signed row):**
  - `test_stage2d_provenance` canonical case and `test_kalman_seed_lineage` modelled case now use recorded funding;
  - `test_pairs_pnl_identity` constant-rate case now expects BORROW_RATE_ASSUMED (the rate is still charged).
- **Mutation:** the funding sign flip is caught by 2 tests.

**Regression:** 165-test cost/provenance set, 1 failure (the constant-rate test above, updated) → all pass.

| Gate | Result |
|---|---|
| G1 | PASS |
| G2 | PASS (known answer, decomposition invariant) |
| G3 | N/A |
| G4 | **PASS for research** (financing from recorded data; fail closed) |
| G5 | PASS |

**Flagged:**
1. **Basis not modelled:** the short leg is priced at spot; perp−spot basis is not modelled.
2. **Live execution unchanged:** it is still spot/margin with `borrow_cost_status = UNAVAILABLE`. Routing the live short leg to perpetuals is a live-trading change and is **not** done (live remains DRY_RUN).
3. **Funding files need a refresh:** the recorded files end 2026-09-19, before the price data (2026-09-24). Trades after that are BORROW_UNAVAILABLE until a refresh, through the sealed writer.


---

## D-7: Verdict ladder replaces SUPPORTED. Status: IMPLEMENTED AND VERIFIED

**`bot/quant_core/verdict_ladder.py`** (`QBP-LADDER-D7-1`): OBSERVATION → STATISTICAL → ECONOMIC → TRADEABLE → CANONICAL.

| Level | Requirements (in addition to every level below) |
|---|---|
| OBSERVATION | effect observed |
| STATISTICAL | dependence-aware test **and** D-3 family confirmation |
| ECONOMIC | net positive **and** cost complete |
| TRADEABLE | d ≥ 1, passes zone V, capacity checked |
| CANONICAL | provenance complete **and** forward-holdout pass |

`ladder_level` climbs while every requirement is True. A False stops it ("failed"); a None stops it as "missing input (capped, never assumed)". The cap reasons go into the explanation.

**Wired:**
- **`research_lab/verdict.py`:** every former SUPPORTED/PARTIALLY_SUPPORTED return goes through the ladder (`VERDICT_CRITERIA_VERSION` 1.0.0 → 2.0.0). A single research-lab test has no registered D-3 family, so the maximum is **OBSERVATION**. The failure outcomes are unchanged, because the sheet replaces SUPPORTED, not REJECTED / INCONCLUSIVE / INSUFFICIENT_DATA / INVALID_RESEARCH / REQUIRES_REVIEW; recorded as the reading of the sheet.
- **`research_lab/tools/research_tools.py`:** the pairs verdict.
- **`research_lab/models.py`:** ladder choices added; the legacy SUPPORTED/PARTIALLY_SUPPORTED choices are kept (labelled "legacy — unverified") so stored rows stay readable. **Migration `0015_verdict_ladder_d7`:** SQL no-op (choices only).
- **`research_lab/evidence_interface.py`:** `SURVIVED_VERDICTS` = STATISTICAL and higher; OBSERVATION and stored legacy SUPPORTED rows (UNVERIFIED, F-11) do not survive.
- **`research_lab/interpreter.py`** wording and **`templatetags/research_extras.py`** colours.

**Not changed (flagged):**
- `bot/research/validated_feature_registry.py` has its own SUPPORTED vocabulary (a separate feature registry, not the research-lab verdict). Owner to confirm whether D-7 extends to it.
- REGIME_DEPENDENT stays a descriptive research-lab state.

**Tests:**
- **`test_d7_verdict_ladder` (6):**
  - order;
  - each level requires all lower levels (a failed family stays OBSERVATION even with perfect economics);
  - missing input caps;
  - a strong-IC, FDR-passing result is OBSERVATION with a family_confirmed cap;
  - an exhaustive input grid over all three verdict functions never produces a legacy verdict or any level above OBSERVATION;
  - the survived set.
- **Updated to the D-7 outcome (8):**
  - `test_research_lab_verdict` (3) and `test_research_lab_pairs_research` (2) now expect OBSERVATION;
  - `test_research_lab_statistical_integrity` (1) now expects OBSERVATION;
  - `test_evidence_interface` (2) now builds survivors with ladder verdicts and asserts that legacy SUPPORTED and OBSERVATION are excluded.
- **Mutation:** the ladder returning SUPPORTED → caught.

**Regression:** 56 research-lab / verdict / scorer modules, 864 tests:
- 8 new failures, exactly the vocabulary assertions above (updated);
- every other failure is in the baseline list;
- after the update, the 68-test rerun passes.

| Gate | Result |
|---|---|
| G1 | PASS |
| G2 | N/A |
| G3 | PASS (no single test can claim more than an observation) |
| G4 | PASS (ECONOMIC / TRADEABLE need complete costs and d ≥ 1) |
| G5 | PASS (legacy rows readable but never "survived") |

---

## D-11: Strategy scorer is descriptive only. Status: IMPLEMENTED AND VERIFIED

- **`engines/strategy_scorer._get_verdict`:** now returns "DESCRIPTIVE SCORE n/100 — not a deployment decision". The health overrides no longer give deployment advice.
- **`backtesting/portfolio_backtester`:** `coins_to_trade` / `coins_to_avoid` are always empty; a new `selection_role` note; the TRADE/AVOID prints removed.
- **`templates/strategy_research.html`:** the two boxes explain that scores are descriptive (D-11) and that eligibility comes from the D-7 ladder.
- Scores, grades and rankings stay for display.
- Regression: covered by the 864-test run above; no test depended on the old wording.

| Gate | Result |
|---|---|
| G1 | PASS |
| G2 | N/A |
| G3 | N/A |
| G4 | PASS (the scorer can no longer recommend a trade) |
| G5 | PASS |

---

## D-6: Point-in-time universe. Status: PARTIALLY IMPLEMENTED (membership rules done; data and floor pending)

**`bot/research/point_in_time_universe.py`** (`QBP-PIT-D6-1`):
- `Listing` (first / last kline, delisting reference);
- `is_member` (listed ≥ 90 days before t, still trading at t, trailing 30-day volume at t ≥ the floor; missing volume = not a member);
- `trailing_volume_at` (window (t − 30 d, t], never extrapolated);
- `universe_at`;
- `universe_version` (a hash including the floor);
- `load_listing_table` (refuses with "BLOCKED" when the table is absent).

**Tests:** `test_d6_point_in_time_universe` (7):
- the 90-day boundary;
- a delisted symbol is in before and out after its last kline;
- the floor is required;
- missing volume refused;
- window coverage;
- future volume cannot change past membership;
- the version changes with the floor and the listings;
- a missing table blocks.

**Mutation:** a 0-day listing age is caught.

**Open (owner):**
1. **The volume floor value:** the sheet says "a fixed floor" with no number; `min_volume_usdt` has no default.
2. **The listing table:** `build_listing_table` (data.binance.vision) needs network access and an owner run; deliberately not executed.

Cross-sectional research stays **BLOCKED** until both are done (sheet text).

| Gate | Result |
|---|---|
| G1 | PASS (implemented part) |
| G2 | PASS |
| G3 | N/A |
| G4 | **BLOCKED** (no listing data) |
| G5 | **BLOCKED** (no universe version until the floor and table exist) |

---

## D-5: DCA significance redesign. Status: PARTIALLY IMPLEMENTED (gated shut)

**`bot/research/dca_significance_d5.py`** (`QBP-DCA-D5-1`):
- **`d5_statistic`:** the single statistic, MTM net return per unit of maximum deployed capital with liquidation at the data end. It reuses `dca_engine._mtm_statistics()[1]` rather than duplicating it.
- **`planted_mean_reversion_path`:** an OU component with a declared half-life and stationary standard deviation on a random walk; amplitude 0 is the null.
- **`require_power`:** refuses any market-data run unless calibration shows ≥ 80 % power at amplitude 5 % for every half-life (24, 72, 168 h).
- **`run_d5_calibration`:** requires the open definitions; not executed.

**Tests:** `test_d5_dca_significance` (6):
- the statistic equals the engine's;
- the OU half-life and SD are recovered (φ within 0.002, SD within 0.005);
- the zero-amplitude null;
- an undeclared amplitude definition refused;
- every gate refusal;
- the calibration requires the definitions.

**Mutation:** a power target of 70 % is caught.

**Open (owner):**
1. **The stationary-bootstrap mean block length** (also used by the D-4 robustness check).
2. **"Matched exposure":** the deployed-capital path over time, or the average exposure.
3. **"Amplitude":** implemented as the stationary SD of the OU component; confirm or replace.
4. **"Smallest effect":** implemented strictly as amplitude 5 % at every half-life.

Until (1) and (2) are fixed, the calibration cannot run and DCA stays **descriptive only**.

| Gate | Result |
|---|---|
| G1 | PASS (implemented part) |
| G2 | PASS (OU known answers) |
| G3 | **BLOCKED** (calibration not run) |
| G4 | PASS (MTM / liquidation accounting) |
| G5 | PASS |

---

## Status of the decision sheet

| Decision | Status |
|---|---|
| D-8, D-1, D-2/D-10, D-3, borrow row, D-7, D-11 | IMPLEMENTED AND VERIFIED |
| D-9 | Confirmed as already implemented (expanding window, 1000 / 168) |
| D-4 | IMPLEMENTED; G3 BLOCKED pending the calibration compute decision |
| D-6 | PARTIAL; blocked on the volume floor and the listing table |
| D-5 | PARTIAL; blocked on two open definitions |

**Edge established: none.** **Live trading: unchanged, DRY_RUN.** **Commit: not done.**


---

## Step 2: Cointegration selection only inside zone S (D-8 + D-3). Status: IMPLEMENTED AND VERIFIED

**Problem (verified in code):** the engine trained on the first 10,000 candles of each pair's whole history (`train_end = min(self.training_window, len(aligned))`) and checked persistence on the rest. That read zone V and later, so its selections could never be canonical (G5).

**Change** (`bot/research/cointegration_engine.py`, `QBP-COINT-ZONE-S-1`):
- **`pair_test_in_selection_zone`:** reuses the engine's own single-pair test (`_test_pair`: Engle–Granger, half-life, persistence — **unchanged**) but feeds it **zone S candles only**.
  - The pair must cover zone S fully; a gap larger than the protocol's frozen `gap_max_candles = 3` → UNTESTABLE, never filled.
  - Seed eligibility is checked with the real `causal_init` (L = 1000 complete candles before the S start).
  - Any sealed-holdout candle is refused.
- **`run_selection_zone_screen`:**
  - every testable pair forms one exploratory BH family (D-3: q = 0.10, zone S, labelled **EXPLORATORY — NOT CLAIMABLE**);
  - the half-life/persistence filters and seed eligibility are applied **after** correction (downgrade-only);
  - at most 5 pairs are nominated for D-3 registration;
  - output carries the zones hash, protocol hash, methodology version and family definition.
- **Legacy `run_all` / `cointegration_pairs.csv`:** kept for reproducibility; every row now carries `selection_basis = LEGACY_FIRST_N_TRAINING_WINDOW`, plus a warning.
- **Research-lab `run_cointegration_test`:** now the zone-S test. An untestable pair → "insufficient zone-S data", so the verdict is INSUFFICIENT_DATA, never REJECTED.

**Tests:** `test_coint_selection_zone` (6):
- zone-V and pre-init prices cannot change the result;
- sealed candles refused;
- partial coverage and a 5-candle gap → UNTESTABLE;
- a short-history pair is not seed-eligible;
- the screen is exploratory, its family = all 6 testable pairs, and only the true cointegrated pair is nominated;
- a true pair without canonical seed history is never nominated.

**Mutation:** using the full history instead of zone S → caught.

| Gate | Result |
|---|---|
| G1 | PASS |
| G2 | PASS (EG/half-life unchanged and reused; zone arithmetic tested) |
| G3 | PASS (one exploratory family; never claimable; nomination cap) |
| G4 | PASS for selection scope (costs enter at the lifecycle stage) |
| G5 | **PASS**: zone-S only, sealed data refused, versions/hashes recorded, legacy output labelled |

---

## Step 3: Legacy pairs paths moved onto run_canonical. Status: IMPLEMENTED AND VERIFIED (two stages gated by owner inputs)

| Path | Before | After |
|---|---|---|
| Research-lab `run_kalman_pairs_test` | Whole-history OLS seed + `run_on_prices` (legacy look-ahead seed, F-03) | Zone-S evidence + `run_canonical` (seed at the S start); **data stop at the end of zone S**; noise overrides flagged as non-canonical protocol |
| Walk-forward | `WalkForwardEngine` re-runs the legacy Kalman per fold | **`canonical_walk_forward`** (`QBP-WF-CANON-1`), per decision lock §2.3–§2.6: one canonical lifecycle run, nothing refit per fold, label truncation d + H = 25 candles, folds entirely in S or entirely in V, development evidence only. **Fold boundaries = owner parameter (no default).** Legacy class kept |
| Permutation | z-timing null (F-05) | Legacy verdict now carries `null_validity` naming F-05 and pointing to the D-4 null; the canonical pipeline uses `pairs_step17_null` |
| D-4 statistic | Mean over the whole trade log | **D-3 endpoint on label-complete zone-V decisions** (`zone_v_label_complete`: V start ≤ t ≤ V end − 25 candles) |
| Pipeline runner | `run_pair_through_ladder` (legacy seed, legacy null, legacy WF) | **`run_pair_canonical`** (`QBP-PAIR-LADDER-CANON-1`), staged and fail-closed: data ≤ V end with the seal checked → `run_canonical` → **recorded funding or stop (BORROW_UNAVAILABLE)** → CANONICAL lifecycle → zone-V endpoint → D-4 null (**BLOCKED until calibrated**) → canonical walk-forward (**BLOCKED without owner folds**) → D-7 ladder (capped without a D-3 family). Legacy function kept, marked LEGACY |

**Tests:** `test_step3_canonical_paths` (9):
- zone-V endpoint boundaries;
- canonical walk-forward known answer with truncation;
- walk-forward refusals (no folds, non-increasing, a fold spanning the embargo);
- the legacy permutation verdict declares F-05;
- a full canonical pair run: seed at the S start, CANONICAL + FUNDING_KNOWN, null BLOCKED, walk-forward BLOCKED, verdict never above OBSERVATION;
- missing funding stops fail-closed;
- sealed input refused;
- the Kalman tool uses the canonical seed and stops at the end of zone S;
- the cointegration tool is zone-S and an untestable pair is INSUFFICIENT_DATA.

**Mutations:** removing walk-forward truncation → caught; letting the Kalman tool read past zone S → caught.

**Observed on the synthetic pair (behaviour check, not evidence):** zone-V net mean return per trade was −0.21 % over 41 trades, so the ladder correctly returns "OBSERVATION not reached — failed: effect_observed". Costs and funding were included.

**Test defects of my own, fixed:**
- a wrong pair-name format (`A_USDT/B_USDT`);
- a timezone error in the test helper;
- one verdict assertion that assumed the effect would be observed.

None was a code defect.

| Gate | Result |
|---|---|
| G1 | PASS |
| G2 | PASS (truncation and endpoint known answers) |
| G3 | **BLOCKED for the null** (D-4 calibration); PASS otherwise |
| G4 | PASS (d = 1 lifecycle, recorded funding, fail-closed financing) |
| G5 | PASS (canonical seed lineage, seal, legacy paths labelled, development-only walk-forward) |

**Still open (owner):**
1. The D-4 calibration compute.
2. Canonical walk-forward fold boundaries.
3. A refresh of the funding files (they end 2026-09-19; trades after that would be BORROW_UNAVAILABLE).
4. The legacy consumers remain callable for reproducibility; their outputs self-identify as LEGACY.

**Regression (guarded):** 18 modules, 233 tests.

| Failure | Classification |
|---|---|
| `test_real_1h_crypto_result_is_byte_identical…`, `test_exactly_target_size_unique_symbols` | Pre-existing (baseline list) |
| `test_research_lab_pairs_research…half_life_too_long…` | **New, real evidence change.** Inside zone S, BTC/BCH has Engle–Granger p = 0.119 (not cointegrated); the old whole-history split called it cointegrated. The test now expects REJECTED and asserts `selection_basis = ZONE_S_D8` |
| `test_kalman_research_lab_integration…synthetic_pair…` | **New, fixture too short for the canonical path** (700 candles, no zone-S coverage → correctly INSUFFICIENT_DATA). The fixture now spans the init window + zone S; the verdict set is the D-7 one |

**Rerun after the updates:** 60 tests (pairs research, Kalman integration, cointegration engine incl. the legacy-label edit, zone-S selection) → **all pass**.


---

## Asset-agnostic market layer (2026-10-04). Status: IMPLEMENTED AND VERIFIED for the mechanism; FX canonical use BLOCKED (owner decisions)

**Why:** the canonical research path assumed a 24/7 crypto venue without saying so. FX data run through it is refused for the wrong reason (weekends read as missing candles) or, worse, could be priced and financed with crypto rules.

**Governing record (read before changing anything):** `STAGE2D_DECISION_LOCK.md` Decision 5 (FX market-closure policy) is **BLOCKED**, with one principle already **locked**:
- A. a *scheduled* closure is not missing data. Its hours are excluded from the grid: no prediction step, no Q accumulation, no warmup count, no z slot.
- B. *unexpected* missing data during market hours follows an FX gap policy whose g_max is an owner value.
- Until the calendar source and the FX g_max exist, FX results are reported as `FX_CALENDAR_UNAVAILABLE`, never as negative evidence, and weekend timestamps are not guessed.

### Assumptions found and how each is classified

**Universal (kept as is):**
- Zones S / embargo / V / F as UTC timestamps; the holdout seal, sealed store and evaluate-once ledger (all four fetchers seal, FX included).
- D-1 timing contract (fill at the next open).
- The D-2/D-10 inference mechanism, the D-3 registry (Holm / BH), the D-7 ladder, D-11.
- Provenance and seed lineage.
- The Kalman equations (`CanonicalKalman`, shared with live, **unchanged**) and the Engle–Granger test.

**Crypto-specific (now stated in `CRYPTO_PROFILE`, behaviour unchanged):**
- The continuous hourly grid: `_align_prices`, `causal_init`'s "complete hourly grid", and `_zone_s_slice`'s clock-time gap.
- g_max = 3 on that grid (D8).
- The seed exactly at S0 (S0 = Sunday 00:00 UTC, a closed hour for FX).
- Recorded perpetual funding (QBP-FUND-1).
- Venue fee + slippage costs.
- The D-6 Binance listing + USDT volume universe.
- Every place where one candle = one clock hour: H = 24; the d + H = 25 truncation in canonical walk-forward and `zone_v_label_complete`; warmup 168; z window 504; L = 1000; embargo 504; D-4 minimum shift 720; the D-10 block length.

**FX-specific (found, not built):**
- Scheduled closures: weekends, holidays, broker server time, DST. The fetchers' `likely_weekend` flag is a heuristic, not a calendar.
- Swap/rollover financing: no history exists; `Instrument.swap_long/short` are None.
- Spread cost: `ForexCostModel` uses a fixed 1 pip divided by the **last close in the file**, one end-of-history price applied to every trade. That is not point-in-time and not canonical-grade.
- Crosses share currency legs, so they are dependent by construction (universe and family definition).
- Metals XAU/XAG sit in the FX registry with different contract specifications.
- History starts 2021-09-20, which gives only 803–837 trading candles before S0 (L = 1000 is unavailable), and ends 2026-09-18.

**Unresolved owner decisions:** listed below.

### What changed

1. **New `bot/quant_core/market_profile.py`** (QBP-MARKET-1):
   - **Calendars:** `ContinuousCalendar` (crypto: the exact grid `_align_prices` always built). `SessionCalendar(closures, source=…)` takes explicit half-open UTC closure intervals and **requires a named source**; there is no built-in FX calendar.
   - **Helpers:** `weekly_closure_intervals` (has no default times), `max_missing_trading_run`, `assert_no_closure_rows`, `assert_continuous_market`. The last is a **refusal-only** guard against session-traded data arriving under a crypto or unknown name; it never classifies an hour as closed.
   - **`MarketProfile`:** calendar, gap_max_candles, seed_rule, horizon_basis, financing_source, cost_model, universe_rule, protocol_hash. Any `None` field means "owner decision missing", and `require_canonical()` then raises `OwnerDecisionMissing("FX_CALENDAR_UNAVAILABLE: …")`.
   - `CRYPTO_PROFILE` describes REVB-4 (protocol hash `3f0da81f…`, unchanged). `FOREX_PROFILE` has all eight fields `None`.
   - **Symbol resolution:** `asset_class_for_symbol` uses the instrument registry first. `profile_for_pair` refuses mixed-class pairs.
2. **`seed.causal_init(…, calendar=None)`:**
   - None or continuous: the unchanged REVB-4 path.
   - Session calendar: the seed is the first trading-grid candle at or after S and must be observed on both legs; θ₀ = OLS on the L trading candles strictly before it.
   - It refuses closure-hour rows, absent trading hours, fewer than L candles, and a missing S start.
3. **`KalmanFilterEngine`:**
   - `_align_prices(…, calendar=None)`: the crypto grid line is unchanged; a session calendar gives its trading grid and refuses closure-hour rows.
   - `run_canonical`: after the seal check, it requires a complete profile, and for crypto it applies the continuity guard.
4. **Zone-S selection (`cointegration_engine`):**
   - `_zone_s_slice(…, calendar, gap_max_candles)` gives the trading-grid gap rule. The FX g_max has no default.
   - `pair_test_in_selection_zone` checks the seal first, then the profile. An undecided FX profile, a mixed pair or a renamed session series gives status **`BLOCKED`** (not UNTESTABLE, not REJECTED; `is_cointegrated = None`).
   - BLOCKED pairs never enter the BH family.
5. **Research lab:** `compute_verdict_pairs` maps BLOCKED to **REQUIRES_REVIEW** ("not actually tested"), and so does the Kalman tool.
6. **`EntryExitEngine`:**
   - Perpetual funding with a non-crypto asset class or symbol: refused (LEGACY too).
   - CANONICAL: refused if the symbols' class differs from `asset_class` (the CRYPTO default can't price FX), and refused unless that class's profile is complete.
7. **`funding.load_funding_series`:** refuses non-crypto symbols.
8. **`point_in_time_universe.is_member`:** refuses non-crypto symbols (D-6 is the crypto universe rule).
9. **`run_pair_canonical`:** a new **market** stage after the data/seal stage; FX stops there with BLOCKED.

**Not changed:**
- `CanonicalKalman` (live/research shared), `CanonicalProtocol` and its hash, the zones, `bot_runner.py` and live pairs.
- The 10 configured pilot pairs were checked: all resolve to the crypto profile.
- No historical result was reclassified, and no FX result was produced.

### Evidence

- **Crypto equivalence:** 14 fingerprints recorded before the change (run twice: deterministic) and after it are **identical**:
  - protocol hash and zones;
  - `run_canonical` with and without short/long gaps, and both seed records;
  - the zone-S test;
  - the full canonical pipeline result, trade log and summary (code-state fields excluded);
  - the research-lab Kalman and cointegration tools;
  - the D-1 label, the D-10 block length, and the funding cost.
- **New tests: `test_asset_agnostic_market` (16; the lab cointegration-tool check was added after the first run).**
  - crypto profile = REVB-4 (pinned protocol hash);
  - the continuous calendar reproduces the crypto grid, seed and gap rule exactly;
  - calendar validation;
  - a weekend gives a missing run of 0, while a 5-hour midweek hole gives 5;
  - the zone-S rule: the crypto rule fails FX on weekends; the trading-grid rule passes; 5 missing trading hours fail; g_max has no default;
  - the shared canonical Kalman on the 24/7 grid re-seeds every weekend and never signals, while on the trading grid it has 0 re-seeds and warmup lasts exactly 168 *trading* candles;
  - a closure-hour observation is refused;
  - the session seed is the first trading candle at or after S (2021-11-07 22:00 on the fixture), exactly L trading candles, unchanged when every future price is altered, with 4 refusals;
  - FX is blocked in selection, the screen, `run_canonical`, the pipeline and the lab, with REQUIRES_REVIEW not REJECTED;
  - the seal fires for FX before any market rule;
  - no leaks: perp funding (loader and engine), the crypto-default CANONICAL engine, the FX CANONICAL engine, and the D-6 universe are all refused for FX;
  - session data under a crypto name is refused, while a 3-day crypto outage still follows the crypto gap rule.
- **Regression:** focused (26 affected modules, 350 tests): 1 failure, pre-existing (`test_instruments…mt5_once_mt5_has_written_the_marker`, failing on the old baseline too). **Full suite (135 files, 1,807 tests, network blocked): 85 failures, the identical set to the pre-change run the same morning, all network/data-freshness tests; zero new.** The 10 configured pilot pairs resolve to the crypto profile.
- **Mutations, 9 of 9 caught** (files restored byte-identical):
  - run_canonical skips the profile;
  - the calendar treats every hour as trading;
  - the seed window includes the seed;
  - perp funding is allowed for FX;
  - selection never blocks;
  - the verdict ignores BLOCKED;
  - the continuity guard is off;
  - the session gap ignores g_max;
  - the pipeline has no market stage.

### Five gates

| Gate | Result |
|---|---|
| G1 Software | **PASS**: new and affected modules tested; mutations 9/9; regression below |
| G2 Math | **PASS**: crypto outputs bit-identical; continuous calendar = old grid/seed/gap; half-open closure intervals; exact warmup boundary; seed window strictly before the seed |
| G3 Statistics | **PASS (scope)**: BLOCKED pairs are excluded from every family; FX is never counted as negative evidence; no inference behaviour changed |
| G4 Quant/trading | **PASS (scope)**: closure hours create no Kalman step; perp funding, crypto costs and the D-6 universe cannot reach FX. FX cost and financing realism are **not** built (owner) |
| G5 Integrity | **PASS**: decision-lock Decision 5 followed (BLOCKED, FX_CALENDAR_UNAVAILABLE, nothing guessed); protocol hash unchanged; profile identity is hashable; no reclassification; no FX result |

### OWNER DECISIONS required before FX can be canonical-capable

1. **OD-FX-1 Calendar source** (Decision 5): a vetted library, or an owner rule naming venue session times, DST handling and a holiday list. It is compiled into `SessionCalendar(closures, source=…)`.
2. **OD-FX-2 FX unexpected-gap g_max** (Decision 5 B): its value. It is not assumed equal to the crypto 3.
3. **OD-FX-3 Seed convention:** confirm the implemented rule ("first *trading-grid* candle at or after S, which must be observed on both legs"; on a 24/7 grid this is exactly the crypto rule), or choose an alternative (e.g. "first *observed* candle at or after S" with a maximum delay).
4. **OD-FX-4 Horizon basis:**
   - whether H (24), the label horizon / truncation (d + H), warmup (168), z window (504), L (1000), embargo (504), D-4 minimum shift (720) and the D-10 block count *trading candles* or *clock hours* (the dependency table's open item "whether closure hours count in H");
   - what a time stop does when the market is closed (the exit can't fill until it reopens).
5. **OD-FX-5 Financing:**
   - the historical swap/rollover source (broker swap history, or rate differentials plus a markup), and the triple-swap day convention;
   - whether a modelled rate may count as "recorded" for CANONICAL.
6. **OD-FX-6 Cost model:** recorded per-bar spreads or fixed pips per pair; rollover-hour widening; a point-in-time reference price (the current model uses the file's last close).
7. **OD-FX-7 Universe:** which symbols (58 files; metals in or out); crosses vs USD legs; the family definition given shared-leg dependence.
8. **OD-FX-8 FX protocol values and version:** whether the REVB-4 numbers (Q, R, P₀, thresholds, f) carry over. Either way, a separate FX protocol hash and methodology version (e.g. STAGE2D-REVB-4-FX) is needed.
9. **OD-FX-9 Data:** refetch MT5 history from before 2021-09 to get ≥ 1000 trading candles before S, and extend it to the end of V. The seal applies to anything at or after F.
10. **(Optional) Unknown symbols:** they keep the legacy CRYPTO default, guarded by the data-shape refusal. A stricter "refuse unknown symbols" would break synthetic test fixtures; your choice.

### Blockers preventing FX from becoming canonical-capable

OD-FX-1 to OD-FX-8 (every `FOREX_PROFILE` field is `None`), plus the OD-FX-9 history shortfall. Until all are resolved, every canonical consumer returns or raises `FX_CALENDAR_UNAVAILABLE`.


---

## D-4 pipeline speed-up (2026-10-04). Status: IMPLEMENTED AND VERIFIED (3.4× faster; calibration still gated)

**Why:** one evaluation of the D-4 statistic (full canonical pipeline on a protocol-length pair, 43,792 candles) took 16.6 s on a quiet machine. The size calibration needs R ≥ 500 pairs × (B + 1) evaluations.

**Profile (before):**
- **Trade simulation's candle loop, 7.0 s:** 4.2 s of it was `iterrows` building a pandas row per candle.
- **Mark-to-market equity curve, 3.0 s:** not used by the statistic.
- **Kalman filter, 2.8 s.**
- **CSV write and read, hashing and provenance, about 3.5 s.**

Most of the time was overhead, not maths.

### Changes (no new libraries; the Kalman maths untouched)

1. **`_scan_candles_canonical`** reads the needed floats from column arrays instead of `iterrows`. Every rule is unchanged.
2. **One simulation core:**
   - `run()`'s steps 2–4 are extracted unchanged into `_simulate()`;
   - the loader's post-read steps into `_prepare_kalman_frame()`;
   - **`EntryExitEngine.run_canonical_frame(results, seed_record)`** is the lean CANONICAL run. It uses the same preparation, lifecycle, costs and funding, in memory: no files, no equity curve, no summary or report.
   - Lineage is validated in-process: the seed must validate under this protocol and name this pair, and the frame must start at the seed candle.
3. **`canonical_net_mean_return_per_trade`** (the D-4 statistic) uses the lean path.
4. **Defect fixed (G2, live/research parity): the canonical Kalman CSV was not read back exactly.**
   - The writer stores full precision, but pandas' default fast float parser is not correctly rounded. About 65% of the Kalman-frame floats came back changed: about 10⁻¹⁶ relative, up to thousands of ulps on near-zero values.
   - So the research trade simulation ran on numbers slightly different from what the filter produced and what live (which never round-trips) uses.
   - `_load_kalman_data` now reads with `float_precision="round_trip"`: 0% changed.

### Evidence

- **Reference:** the committed code (105e256) in a separate worktree, compared on 10 protocol-length datasets: 5 random walks, 2 cointegrated pairs (φ = 0.97 and 0.9), 2 with short gaps plus a gap long enough to force a re-seed, and 1 with a 3-day funding hole.
  - **Step 1 (array scan):** trade log, signal log, summary, equity curve, every net P&L and the statistic are identical on all 10.
  - **Step 2 (exact reader + lean statistic):** trade count, every recorded net P&L, summary, equity and the **statistic are identical on all 10**.
    - The saved trade-log and signal-log *files* differ only in diagnostic columns (entry β/α/spread/quantities/prices; signal-log z) by 2×10⁻¹⁶ to 5×10⁻¹³ relative. These are the filter's true values now that the CSV parse noise is gone.
    - No trade decision changed.
  - **Crypto fingerprints:** only `trade_log` bytes changed, for the same reason. The summary, pipeline result, research-lab outputs, labels and funding are identical.
- **Speed (quiet machine):** **16.6 s → 4.9 s per evaluation**, statistic −0.009080215116279072 before and after.
  - Remaining: Kalman 2.8 s (shared live/research `CanonicalKalman`, deliberately untouched); data-version hash 1.0 s (provenance); scan 0.9 s.
- **Tests (in `test_d4_step17_null`, 5 new; no new test file):**
  - the lean trade log equals the file pipeline exactly, and the lean statistic equals the file statistic exactly;
  - lean lineage refusals (wrong rule set, wrong pair, foreign methodology seed, frame not at its seed);
  - the canonical CSV round-trips bit-exactly;
  - funding gaps and the borrow flag match the file run.
- **Mutations, 5 of 5 caught** (files restored byte-identical):
  - seed validation skipped;
  - inexact parser;
  - lineage dropped;
  - the scan reads the wrong column (caught by 8 lifecycle and live/research parity tests; the equivalence test can't see a bug shared by both paths);
  - funding-gap rule skipped (initially **not caught**; the funding-gap parity test was added, and it is now caught).

### Calibration compute estimate (revised; this PC, 8 logical cores, an assumed ~5–6× effective parallel speed-up)

| Setting | Evaluations | Before | Now |
|---|---|---|---|
| size, B = 99, R = 500 | 50,000 | ~1.5–2 days | **~11–14 h** |
| size, B = 999, R = 500 | 500,000 | ~15–20 days | **~4.5–6 days** |
| power calibration | — | +0.5–1 day | +~4–8 h |

### Five gates

| Gate | Result |
|---|---|
| G1 | **PASS**: equivalence harness, new tests, mutations 5/5; full suite (after the DCA restore) 1,811 tests, 85 failures = the known baseline set, 0 new |
| G2 | **PASS**: statistic and P&L bit-identical; CSV parse defect fixed (exact round trip proven) |
| G3 | **PASS**: the null and its statistic are unchanged; the calibration gate is still closed |
| G4 | **PASS**: same lifecycle, costs and funding (funding-gap behaviour proven equal) |
| G5 | **PASS**: in-process lineage validation; the lean path writes no artifact; the reference comparison is recorded |

### Not done (owner choice)

- **Further ~3–4×** would need: (a) a faster `CanonicalKalman` step (shared with live, so it needs a bit-identity proof against the current code over many runs, or it isn't acceptable for parity); (b) a faster data-version hash (provenance code, same hash required); (c) a parallel calibration runner.
- **No calibration was run.**


---

## Incident: silent revert of `dca_engine.py` captured by commit 105e256 (2026-10-04). Status: RESTORED

**What happened:**
- At 13:31:35 on 2026-10-04, outside this session's edits and not intended by the owner, `bot/research/dca_engine.py` was overwritten with the older 1,144-line version, byte-identical to commit `458ac14` (Stage 2D baseline).
- That removed the post-audit Phase 2 fixes (B1–B8: next-open fill model, censored data-end cycles, `return_on_max_deployed` in place of `r_multiple`, B = 999 single-null permutation reported as raw evidence only, configured-level exhaustion metric, calibration-window volatility scaling, financing status), plus `_mtm_statistics`, which D-5 reuses.
- The morning full suite (finished 13:04) had passed against the correct 1,639-line version (`757578d`).
- Commit `105e256` ("commit everything", 14:54) captured the revert (`dca_engine.py`: −613 / +118 lines). The commit summary was not checked file by file. This was a verification failure.

**How it was found:**
- The next full suite: `test_dca_phase2` could not import; `test_phase3_engineering` failed on `return_on_max_deployed`.
- One test part (about 540 tests) did not run, because `test_dca_engine` could not load.
- An external review written against the reverted file reported defects that `757578d` had already fixed.

**Fix:**
- `git checkout 757578d -- bot/research/dca_engine.py`, verified identical to `757578d` (1,639 lines). The reverted copy is kept as a scratch backup.
- No intended edit to `dca_engine.py` was made after `757578d`: D-5 only reads `_mtm_statistics`.
- **DCA tests:** `test_dca_engine`, `test_dca_phase2`, `test_d5_dca_significance`, `test_phase3_engineering` (86 tests) all pass. Full suite: 1,811 tests, 85 failures = the known network/data-freshness baseline set, 0 new.
- Only this file was affected: it is the only unexpected file modified in the 13:05–14:50 window, and the other files with net deletions in `105e256` are intended D-11 / D-2 edits.

**Process change:** before every commit, inspect the per-file numstat. Flag files the work did not touch, and any file whose content equals an older commit's version. Run the full suite after the last edit.

**Status of the DCA engine (`757578d`) against the five gates:** unchanged from the remediation report.
- **Accounting:** fixed.
- **F-08 (power):** BLOCKED under D-5 (needs the owner's definitions of matched exposure and planted-effect amplitude).
- **Provenance (run IDs):** open.
- DCA stays descriptive only.


---

## DCA five-gate remediation, Step 2 (2026-10-04). Status: IMPLEMENTED AND VERIFIED (D-5 power still BLOCKED, owner)

**Basis:**
- An external review of `dca_engine.py` (written against the reverted file).
- A Step-1 re-audit of the restored version (`757578d`) by behavioural probes: 11 items confirmed already fixed (next-open clock with no same-candle re-entry, cost/quantity weighted average, LONG/SHORT P&L, per-fill fees, `r_multiple=None`, censoring in the DCA summaries, configured-level exhaustion, calibration-window volatility scaling, B = 999 without an edge claim, financing status).
- That left 7 open defects, fixed here. No feature was added.

| ID | Defect (verified by probe) | Fix | Gate |
|---|---|---|---|
| F1 | `evaluate_strategy_oos` counted DCA's censored fold-end cycles (liquidation marks) as completed trades; on a synthetic walk-forward, 5 of 15 "trades" | Trades flagged `censored=True` are excluded from trade statistics (count, win rate, expectancy, profit factor, R, Sharpe/Sortino, returns, fees) but kept in mark-to-market `max_drawdown_pct` / `final_balance`, and reported as `n_censored_trades` / `censored_mark_pnl` (fold and aggregate) under `CENSORED_TRADE_POLICY`. Only the explicit flag triggers it. | G3, G4 |
| F2 | The feature experiments averaged censored marks into "eventual cycle profit" (probe: −32.67 vs 1.0) and data-end time into "hours to resolution" | Outcome columns use resolved cycles only; `n_censored_cycles_touched` is reported; events carry `cycle_censored`; an events frame without censoring information is **refused** (fail closed) | G3 |
| F3 | The evaluator's `avg_r_multiple` reported 0 for trades without R | None when no trade has R (fold and pooled); unchanged when trades carry R | G3 |
| F4 | DCA simulation accepted sealed forward-holdout candles | `ZONES.assert_no_sealed_data` on every DCA simulation (both clocks) | G5 |
| F5 | DCA research outputs had no evidence status | `DCA_EVIDENCE_LABEL`: development evidence, PSEUDO_HOLDOUT, not claim-eligible (CRRP; D-5 gate closed; a claim needs a D-3 family). A zone-S/V DCA confirmatory protocol is **not** invented (it belongs to D-5, owner). | G5 |
| F6 | No provenance | `dca_run_provenance()` reuses `bot.quant_core.provenance`: method version `QBP-DCA-PHASE2-2`, config hash, content data version, zones hash, code state, deterministic run id; attached to the permutation test and suite | G5 |
| F7 | FX DCA ran on the 24/7 crypto clock with weekends as gaps, spread-only cost and no swap | `build_config_with_real_costs` / `compute_volatility_scale_factor` require a complete market profile (FX gives `FX_CALENDAR_UNAVAILABLE`); the simulator refuses session-traded data (`assert_continuous_market`) | G4, G5 |

### Evidence

- **Other strategies unchanged:** the evaluator output (random `run_id` excluded) for the scripted strategy with and without R, and for the real-BTC Backtester adapter, is **identical** between committed `41ed7dd` and this change (the reference worktree was re-run, not the earlier non-deterministic fingerprint).
- **DCA changed as intended:** on the synthetic walk-forward, losses went 4 → 0 and expectancy −0.18 → +0.30 once the fold-end marks left the trade statistics. Every "loss" had been an open cycle. Those losses now appear in mark-to-market drawdown and `censored_mark_pnl`. **Completed-cycle statistics must never be read without the censored figures** (the high-win-rate trap).
- **Tests:** 9 new in `test_dca_phase2`. Two existing fixtures gained `cycle_exit_reason` (justified: the function must now know which cycles are censored, and those fixture cycles are resolved).
  - DCA suites: 95 tests pass.
  - Evaluator suites (`test_strategy_oos_evaluator`, `test_oos_validator`) pass.
- **Full suite:** 1,820 tests, 85 failures = the known baseline set, 0 new.
- **Mutations, 8 of 8 caught** (files restored byte-identical): censored flag ignored; mark dropped from drawdown; undefined R as 0; censored outcomes kept; no seal; no session guard; FX profile not required; config left out of the run id.

### Five gates (DCA engine after Step 2)

| Gate | Result |
|---|---|
| G1 | **PASS** |
| G2 | **PASS** (accounting known answers) |
| G3 | **PARTIAL**: inference hygiene fixed; **D-5 power (F-08) BLOCKED**: needs the owner's definitions of matched exposure and planted-effect amplitude |
| G4 | **PASS** for crypto LONG. SHORT stays cost-incomplete (financing unavailable; whether recorded perp funding applies is an owner decision). FX BLOCKED. |
| G5 | **PASS**: seal, evidence label, provenance, FX gate |

DCA remains **descriptive only**; no DCA claim is possible until D-5 calibrates.

### Observation (not changed; owner)

The evaluator's `worst_fold_max_drawdown_pct` reads 0.0 for every non-DCA run, including a real BTC Backtester run with −$15,034 net. This looks like a pre-existing drawdown bug in the shared evaluator, outside this scope. Investigated with owner approval: root cause found (see the next entry).


---

## Defect: OOS evaluator fold drawdown always 0.0 % (2026-10-04). Status: FIXED

**Found:** while verifying DCA Step 2, the evaluator reported `worst_fold_max_drawdown_pct = 0.0` for every non-DCA run, including a real BTC Backtester run with −$15,034 net. The owner approved an investigation.

**Root cause:**
- Phase 3 fix M-02 (`ef5636b`, 2026-10-02) changed `Backtester._build_results()` to report `max_drawdown_mtm`.
- `oos_validator._compute_trade_metrics()` builds a throwaway Backtester and injects only the old `max_drawdown` attribute. So `max_drawdown_mtm` stayed at its 0.0 default.
- **Every fold's `max_drawdown_pct` read 0.0 %.**
- **Real BTC proof:** 8 folds reported 0.0 % against closed-trade drawdowns of 10.3–26.7 % (fold 6: −$2,670, 26.7 %). Risk was hidden (G4).

**Fix:**
- `max_drawdown_pct` = the closed-trade balance peak-to-trough (`_replay_fold_bookkeeping`, the evaluator's own replay).
- The new field `max_drawdown_basis` states what it is: intra-trade mark-to-market dips are **not observable from a trade list**. For a fold with censored trades it is `..._PLUS_CENSORED_MARKS` (DCA Step 2).

**Affected artifacts:** none stored. No saved output of the strategy-to-outcome evaluator exists after `ef5636b`.
- The September cross-sectional checkpoints predate the defect and use the Type C evaluator; their drawdowns are non-zero and real.
- The pairs-engine summaries compute their own drawdown.
- The defect affected only results computed on demand.

**Evidence:**
- **Field-by-field diff against committed `7e9645c`** (random `run_id` excluded): for the scripted strategy with and without R, the real-BTC Backtester and DCA, **only the drawdown fields changed**. Real BTC worst fold went 0.0 % → 26.70 %; DCA was already correct through its censored mark-to-market path, so only the basis label was added.
- **Tests (`test_strategy_oos_evaluator`, 2 new):** +100 / −300 / +50 on 10,000 gives exactly 2.9703 % (fold and aggregate); all-winning gives 0.
- **Mutation:** reading the never-set field again is caught.
- **Full suite:** 1,822 tests, 85 failures = the known baseline set, 0 new.

| Gate | Result |
|---|---|
| G1 | PASS |
| G2 | PASS (known answer) |
| G3 | PASS |
| G4 | **PASS**: drawdown no longer hidden; basis labelled honestly |
| G5 | PASS: no stored artifact affected; cause traced to a commit |


---

## Parallel zone-S selection screen (2026-10-05). Status: IMPLEMENTED AND VERIFIED (not run on the universe)

**What:**
- `run_selection_zone_screen(..., n_workers=k)` tests pairs in k worker processes.
  - Each worker receives the universe's closes once (initializer); a task carries only the two symbol names.
  - `ProcessPoolExecutor.map` keeps the original pair order.
  - The exploratory BH correction (D-3) runs once, in the parent, over the merged rows, exactly as in the serial path.
  - A worker's exception (e.g. a sealed-holdout candle) is re-raised in the parent; nothing is skipped.
- `run_selection_screen_on_data(data_dir, symbols, n_workers, output_path)` loads `*_USDT_1h.csv` closes (exact float parser) and runs the screen. It refuses to overwrite an existing result file (opened with "x"). Statistics, gates and labels are unchanged: exploratory, not claimable.

**Accuracy:** each pair test is a pure, deterministic function of its two series.
- **Synthetic universe:** serial and parallel outputs are identical (full serialized-output comparison).
- **Real data:** 20 symbols / 190 pairs, and 12 symbols / 66 pairs with 2, 3, 4 and 8 workers: **byte-identical every time**. Only equality and timings were printed (no p-values), so this verification exposed no research results.

**Speed on this machine** (Intel i5-8365U: 4 physical cores / 8 threads, 15 W, Balanced plan), 66 real pairs:

| Workers | Time | Speed-up |
|---|---|---|
| serial | 57 s | — |
| 2 | 46 s | 1.25× |
| 3 | 45 s | 1.28× |
| 4 | 51 s | 1.13× |
| 8 | 72 s | 0.78× |

- **Cause:** the Engle–Granger test's ADF autolag (OLS by SVD) is arithmetic-bound. Hyperthreads share arithmetic units, and a 15 W chip loses its turbo under all-core load.
- **Measured serial cost:** about 0.9 s per pair (an earlier 2 s estimate came from cold calls). The full 150-symbol screen (11,175 pairs) takes **about 2.8 h serial, about 2 h with 3 workers** here. On 8+ physical cores, scaling should be close to linear.

**Tests (`test_coint_selection_zone`, 3 new):** parallel equals serial exactly; a worker refusal is raised; an existing result file is never overwritten.
**Full suite:** 1,825 tests, 85 failures = the known baseline set, 0 new (the run finished; Claude Code later stopped the idle shell for low memory).
**Mutations, 2 of 2 caught:** results out of order; a worker refusal swallowed.

| Gate | Result |
|---|---|
| G1 | PASS |
| G2 | PASS (identical outputs) |
| G3 | PASS (one BH family, unchanged) |
| G4 | n/a |
| G5 | PASS (no overwrite; seal enforced in workers; no results exposed while verifying) |


---

## Defect: a test overwrote real research outputs on every full-suite run (2026-10-05). Status: FIXED

- **Found** while checking the dashboard's Alpha Intelligence panel: all 155 `research_data/*_cross_section.csv` files had a 2026-10-04 22:15–22:19 timestamp, which fell inside a full-suite run.
- **Cause:** `test_run_cross_sectional_oos_forex.AssetClassDispatchTest` ran `run_cross_sectional_research()` with its default `research_data_dir="research_data"`. The crypto test rewrote 155 `research_data/*_cross_section.csv` files and the forex test rewrote 58 `research_data/forex/*_cross_section.csv` files on **every** full-suite run, in this and earlier sessions. During the 2026-10-05 suite these 213 files were the only repository files written (checked across `research_data/`, `data/` and `logs/`).
- **Fix:** both tests now pass a temporary `research_data_dir` (deleted afterwards). Real price data is still only read. Each test asserts that none of the 213 real outputs changed (mtime and size snapshot).
- **Verified:** the test module passes (5 tests); 0 files in `research_data/` or `data/` were written during the run. The guard detects a changed file (checked by shifting one file's mtime, then restoring it exactly). A deliberate re-break was **not** run, because re-breaking would overwrite the real files again.
- **Consequence (G5):** the original cross-section outputs are **lost**. They are git-ignored, no backup exists anywhere in the project, and the engine writes no provenance sidecar. The files now on disk are **regenerations by the code as of the 2026-10-05 suite run, not original research outputs**. They can be rebuilt deterministically from `data/` by the cross-section engine, so no research input is lost. Any earlier result that cited them is not reproducible from these files as-is.


---

## Provenance for the non-canonical research engines (2026-10-05). Status: IMPLEMENTED AND VERIFIED

- **Why:** the cross-section, feature-validation, contagion and PCA engines wrote outputs with no provenance at all. When a test overwrote 213 cross-section files, nothing could establish what had produced the originals.
- **What:** `bot/quant_core/provenance.py` gains
  - `input_files_fingerprint` (sha256 of each input file plus a set version);
  - `input_frames_fingerprint` (QBP-DATAV1 content version of in-memory inputs);
  - `write_research_output_provenance`: a sidecar recording inputs, config and config hash, code state and a deterministic run id.
- **Status:** always **LEGACY** (methodology `NON-CANONICAL-RESEARCH-ENGINE`, evidence status descriptive). Fingerprints and code state are computed once per run. A sidecar failure is logged, not raised.
- **Wired into:**
  - `cross_section_engine` (crypto and forex runs; each output lists the whole universe's input files);
  - `run_research_all` (observations; the pooled and per-symbol validated features, whose inputs are all symbols because of the family-wide correction);
  - `contagion_engine` (provider-loaded inputs → QBP-DATAV1);
  - `pca_engine.save_outputs` (all 4 files).
- **Tests (`test_stage2d_provenance`, 5 new):**
  - fingerprints exact and order-independent;
  - sidecar LEGACY and complete, run id deterministic and config-sensitive, rejected as canonical;
  - a failure is logged, not raised;
  - a real cross-section run on temporary data names exactly its input files;
  - PCA's 4 sidecars.
- **Mutations, 3 of 3 caught:** inputs not recorded; config left out of the run id; PCA sidecars removed.
- **Related tests:** 94 run (provenance, cross-section, PCA, contagion, stability gate, dataset explorer, D-1 labels), all pass. No file search in the code base matches a `.provenance.json` sidecar by accident.
- **Not integration-tested:** `run_research_all` (multiprocess, all symbols) and `run_contagion_research` (provider loading). Both use the same helper, covered above.


---

## Kalman P0 sensitivity (2026-10-06). Status: DESCRIPTIVE ANALYSIS (no code change)

**Setup:** synthetic pairs only (constant, step and drifting true beta, plus unrelated random walks; 2 seeds each), canonical `run_canonical` with the causal seed at S0. Q and R are fixed at baseline; only `p0_scale` varies (0.01, 0.1, 1 [baseline], 10, 100). Each run is compared with the baseline on post-warmup candles (20,856 per run). No market data, no zone V, no P&L; script in the session scratchpad.

| P0 | Well-identified (constant / step beta) | Weakly identified (drifting / unrelated) |
|---|---|---|
| ×10, ×100 | back to baseline beta within ~650 candles; max \|Δz\| ≤ 0.035; entries ≥ 99.3 % identical | ~6,500–7,400 candles; max \|Δz\| ≤ 0.24; entries ≥ 98.4 % |
| ×0.1 | ~3,900 candles; \|Δz\| ≤ 0.15; entries ≥ 99.3 % | 12,800–13,600 candles; \|Δz\| ≤ 1.06; entries ≥ 91.7 % |
| ×0.01 | ~6,900 candles; \|Δz\| ≤ 0.29; entries ≥ 98.9 % | 18,300–20,300 candles (most of zone S); \|Δz\| ≤ 2.3; entries ≥ 80 %; beta RMSE 0.129 vs 0.022 |

**Reading:**
- The baseline P0 = 1 lies in the **robust** region: larger values barely change the output.
- Smaller values make the filter overconfident in θ₀, so it adapts slowly. On weakly identified pairs, where (β, α) are nearly collinear because log price varies little relative to its level, the seed's influence then persists for most of zone S. This matches the earlier Q/R finding that P0 matters most on weakly identified pairs.

**Recommendation (owner decision; nothing changed):** keep P0 = 1. Report any canonical pairs result with a P0 = 10 robustness check. Treat P0 < 1 as a protocol change.


---

## FX data clock fix and FX calendar (2026-10-06). Status: IMPLEMENTED AND VERIFIED (live offset confirmation pending)

**Owner decisions:**
- **Option A** "fix the data clock".
- **OD-FX-1 calendar:** the owner chose "closed Friday 22:00 to Sunday 22:00". It is implemented as **closed Friday 17:00 to Sunday 17:00 America/New_York**, which is that rule in winter and follows US daylight saving (21:00 UTC in summer), as recommended and approved with option A. **No holiday list** has been chosen.

**Defect found:**
- `mt5_data_fetcher` stored MT5 bar times with `pd.to_datetime(time, unit="s", utc=True)`. MT5 bar times are the broker **server** wall clock, so all 58 `data/forex/*_1h.csv` files were labelled UTC while really being server time.
- **Evidence:** every weekend is Friday 23:00 → Monday 00:00 (metals: Monday 01:00) in all seasons, including every spring and autumn window where US and EU daylight-saving dates differ. So the server clock follows US daylight saving: **server = America/New_York + 7 h** (UTC+2 winter, UTC+3 summer). The only exceptions are Christmas and New Year.
- This is inferred from the data and **not yet confirmed live**: run `confirm_mt5_server_clock()` with the terminal open.

**Fix:**
- `mt5_server_time_to_utc()` (`MT5_SERVER_CLOCK`, default "NY+7") is used by both fetch paths. It fails closed on non-existent or ambiguous New York times.
- `convert_server_clock_files()` converted the existing files: originals and markers copied to `data/forex/_archive_server_clock_2026-10-06/` (115 files); only files with the server-time signature are converted (all 58 had it); markers rewritten with a `clock` field.
- EUR_USD had **no** provenance marker. Its MT5 origin is inferred from the identical signature and start time (2021-09-20 03:00 server = 00:00 UTC), and its marker says so.
- `FOREX_PROFILE.calendar = fx_new_york_close_calendar()`. The other FX rules are still undecided, so FX stays FX_CALENDAR_UNAVAILABLE for canonical use.

**Verification:**
- **Known answers:** the winter and summer Monday open and Friday close land on 22:00/21:00 UTC and 21:00/20:00 UTC; an impossible New York time is refused.
- **Converted data vs calendar:** **0 rows inside a closure across all 58 files** (before conversion, every Friday's 22:00 and 23:00 bars fell inside it). There are 16,357 missing trading hours, 10,355 of them Christmas/New Year (no holiday list chosen).
- **Metals (XAU, XAG):** about 1,470 further missing hours each, from the daily 17:00–18:00 New York metals break. Metals need their own calendar (owner, with OD-FX-7).
- **Tests (`test_asset_agnostic_market`, 3 new + 1 justified update):** conversion known answers; calendar daylight-saving boundaries; the one-off conversion archives, converts exactly, leaves non-MT5 files untouched and refuses a second run. The profile test now expects the calendar decided.
- **Mutations, 3 of 3 caught:** a 6 h offset; a calendar without daylight saving; originals not archived.
- **Second test-isolation defect found:** `test_instruments.ProvenanceMarkerTest` wrote, and in `tearDown` **deleted**, the REAL `data/forex/EUR_USD_1h.provenance.json` on every run. That is why EUR/USD was the only MT5 file without a marker, and it deleted the new marker again during this verification.
  - **Fixed:** the class now runs against a temporary data folder (`instruments.DATA_DIR` swapped) and asserts the real marker is untouched.
  - **Test updates (justified):** three tests asserted EUR/USD = Yahoo, a state the deleting test had created; they now assert the venue the real marker records.
  - The previously failing `test_forex_registry_reports_mt5_once_mt5_has_written_the_marker` now passes.
  - **Mutation:** with the folder swap disabled, the tests fail, and the real marker stays untouched.
  - The real EUR/USD marker was restored, with the history noted.
- **Forex regression:** 102 tests run. Remaining failures are known baseline ones only (Yahoo live-network tests; `test_universe_is_currency_balanced_not_usd_dominated`). The 4 failures caused by the marker defect pass after the fix (30 affected tests re-run, all OK).

**Affected existing outputs (G5; nothing deleted).** These were computed from the server-clock timestamps (2–3 h late, with daylight-saving seams):
- `research_data/forex/*_cross_section.csv` (58);
- `research_data/forex_pca_*.csv` (4);
- `research_data/forex_cointegration_pairs*.csv` (7);
- `research_data/forex_cross_sectional_family_verdict.json`.

They are LEGACY on the server clock and are not comparable with crypto or with the corrected data. Within-FX statistics are shifted rather than scrambled, but any cross-asset alignment was 2–3 h off. The forex dashboard's cross-section "as of 2026-09-18 22:00 UTC" was a server-clock label until those files are regenerated.

**Still owner decisions for FX:** gap limit (OD-FX-2), seed rule (OD-FX-3), horizon basis (4), financing (5), spread model (6), universe incl. metals and their calendar (7), protocol values (8), older history (9), holiday list (optional).


---

## FX history fetch, OD-FX-9 (2026-10-06). Status: DONE

- **Owner-approved.** Through the logged-in MT5 terminal (read-only attach, no stored credentials): `download_all_mt5_symbols(date_from="2021-05-01", attach_only=True, archive_dir=...)`. The new optional arguments keep the old behaviour by default. Previous files and markers were archived in `data/forex/_archive_pre_history_2026-10-06/`.
- **The first pass left 53 of 58 symbols ending 2026-09-18:** MT5 serves its local cache and only syncs a symbol when it is requested. A sync request (`copy_rates_from_pos`) brought all 53 up to date, and a second pass rewrote the files.
- **Result (validated):**
  - 58 of 58 pairs, none stale;
  - history from 2021-05-02 21:00/22:00 UTC (the Sunday open) to 2026-10-02 20:00 UTC (the last bar before F);
  - at least 3,093 trading candles before zone S (L = 1000 needed);
  - zone V fully covered;
  - 0 rows at or after F in the research files (all sealed: 58 files in `_sealed_holdout_F2026-10-04`);
  - 0 rows inside calendar closures.
- **Tests:** 96 forex/data tests run; only the known baseline failure remains. The live-terminal download path itself has no automated test (tests never contact an exchange).
- **Remaining for an FX zone-S screen:** OD-FX-2 (gap limit), OD-FX-3 (seed rule), OD-FX-7 (universe, incl. metals and their daily break).


---

## FX selection rules OD-FX-2/3/7 + holiday list (2026-10-06). Status: DONE (owner-delegated)

- **Owner-delegated ("do what's recommended").** Each choice is the strictest defensible option, recorded here as owner-delegated rather than owner-chosen.
  - **OD-FX-2, gap limit:** `gap_max_candles = 5` missing TRADING hours, counted on the FX trading grid (scheduled closures are not gaps; Decision 5 B). It is data-informed and was measured on zone S for the 51 universe pairs.
    - 46 of 51: the longest hole is exactly the 5 h broker-wide outage on 2021-12-02.
    - 5 of 51 go over the limit, so every pair containing them is UNTESTABLE in the screen:
      - USD/SEK and EUR/SEK: 24 h from 2023-01-01 22:00 UTC;
      - USD/NOK and EUR/NOK: 17 h from the same time;
      - USD/THB: 6 h from 2023-12-22 16:00.
    - The SEK/NOK holes are the per-symbol Scandinavian New Year closure, not broker-wide, so they are not added to the shared calendar. A per-symbol holiday list would be an owner decision.
    - A looser limit would admit unmeasured holes; a tighter one would discard every pair for one shared outage.
  - **OD-FX-3, seed rule:** `FIRST_TRADING_CANDLE_AT_OR_AFTER_S`. The causal seed sits at the first trading candle at or after S0, never before S and never interpolated across a closure. This is the same strict causal rule as crypto, applied on the trading grid.
  - **OD-FX-7, universe:** `FX-FLOATING-51 v1`, the 51 MT5 currency pairs excluding hard pegs (HKD: USD/HKD, EUR/HKD; DKK: EUR/DKK, GBP/DKK, USD/DKK) and metals (XAU/USD, XAG/USD).
    - Pegs are excluded because a policy-pegged pair is cointegrated by construction, so a "discovery" there is not evidence.
    - Metals are excluded because they need their own calendar (daily break), which is not decided.
    - Pairs outside the universe are BLOCKED (OwnerDecisionMissing), never UNTESTABLE or rejected.
  - **Holiday list (optional item of OD-FX-1):** 11 UTC closure intervals (9 Christmas/New Year events, 2021–2026) measured from the data. The 2021-12-02 outage is deliberately NOT a holiday, so it still counts against g_max.
- **Code:**
  - `bot/quant_core/market_profile.py`: `SELECTION_FIELDS`, `require_selection()`, `FX_HOLIDAY_CLOSURES`, `fx_new_york_close_calendar(holidays=True)`, `FX_UNIVERSE`, `in_universe()`, and FOREX_PROFILE values.
  - `bot/research/cointegration_engine.py`: the FX selection path uses the FX calendar for alignment, seed and gap; rows are tagged `selection_mode = FX_SELECTION_ONLY`; it is exploratory and never claimable.
  - `bot/research_lab/tools/research_tools.py`: the Kalman tool now returns REQUIRES_REVIEW (with the FX_CALENDAR_UNAVAILABLE reason) instead of raising.
- **Still blocked for FX (canonical):** Kalman state, lifecycle and evidence. Horizon basis (OD-FX-4), financing (5), spread/cost model (6) and protocol values (8) are undecided, so `require_canonical` still refuses FX. Crypto is unchanged (golden fingerprints identical apart from the known trade_log difference).
- **Tests:** `test_asset_agnostic_market` was updated. Every changed assertion follows the decided rules; none were loosened.
  - New: in-universe pair OK and seed-eligible, a peg BLOCKED; a 5 h hole gives OK and a 6 h hole gives UNTESTABLE; holiday and outage probes.
- **Mutation checks (6/6 caught):** gap limit 6; universe not enforced; holidays dropped; outage relabelled as a holiday; FX through the canonical gate; lab catch removed.
- **Regression:** 120 related tests plus 4 MT5 tests, offline with no network attempts. Only the known baseline failure `test_universe_is_currency_balanced_not_usd_dominated` remains (it predates this change; its module path imports none of the changed files).


---

## 2026-10-06 — FX ZONE-S SELECTION SCREEN (EXPLORATORY, NOT CLAIMABLE)

- **Run.** `run_selection_screen_on_data("data/forex", FX-FLOATING-51, n_workers=3)` at commit 25283c4. Output: `research_data/selection_screen/fx_screen_2026-10-06.json` (new file, opened with "x"). Runtime 1,101 s.
- **Result.**
  - 1,275 pairs requested: 1,033 testable, 242 UNTESTABLE (gap rule), 0 BLOCKED, 0 seed-ineligible.
  - One exploratory BH family (q = 0.10) over the 1,033 testable pairs, Engle–Granger on zone S: **0 BH survivors, 0 nominated**.
  - Raw (unadjusted) EG p < 0.05: 56 of 1,033 (5.4 %). That is about the rate expected by chance across this many tests; the pairs share currencies, so the tests are not independent and this is descriptive only.
  - Reading: no FX pair is distinguishable from multiple-testing noise under this screen. That is "edge not established", not proof that no relationship exists. Nothing advances to V or F.
- **UNTESTABLE breakdown.**
  - 240 pairs contain a symbol with its own zone-S hole longer than 5 h: USD/SEK, EUR/SEK, USD/NOK, EUR/NOK, USD/THB.
  - The other 2 (EUR/PLN–USD/CZK and USD/CZK–USD/PLN) have no single-symbol hole above 5 h, but their joint (inner-joined) series has a 7 h hole where adjacent holes of the two legs meet. This is correct: the rule applies to the series the test actually uses.
- **CORRECTION to the entry above ("FX SELECTION RULES").** The 2023-01-02 SEK/NOK hole is NOT a Scandinavian market closure, as I had called it. NOK/SEK, SEK/JPY and NOK/JPY were quoted normally through that day, and only the four USD/EUR crosses of SEK and NOK are missing (no other universe pair is). It is a broker feed gap on those four symbols with unknown cause; the USD/THB hole (2023-12-22 16:00–21:00 UTC) is likewise unexplained. Neither is a holiday, so neither may be added to the calendar. Re-admitting these pairs would mean loosening the rule after seeing data, which is an owner decision.


---

## 2026-10-07 — CRYPTO ZONE-S SELECTION SCREEN (EXPLORATORY, NOT CLAIMABLE)

- Run: run_selection_screen_on_data("data", all 155 *_USDT_1h files, n_workers=3). Output research_data/selection_screen/crypto_screen_2026-10-07.json (new file, "x"). Runtime 16,734 s.
- 11,935 pairs: 11,026 testable, 909 UNTESTABLE (459 do not cover zone S; the rest have Binance data gaps of 4/8/18 days inside S, beyond the 3-candle policy). One exploratory BH family (q = 0.10) over the 11,026, Engle-Granger on zone S.
- Raw EG p < 0.05: 3,620 of 11,026 (32.8 %), far above the ~5 % expected under the null. BH survivors: 2,863. After the half-life/seed filters: 277. Nominated (cap 5): AVA/PUNDIX, DIA/FIL, DIA/IOTX, DIA/LPT, DIA/ONT (half-lives 33-62 h, all seed-eligible).
- Reading (descriptive): a raw rejection rate of 33 % means either widespread zone-S cointegration (crypto shares a dominant market factor and the 2022 bear trend) or EG size distortion on heavy-tailed, break-prone crypto series. The screen cannot tell which. Survivors are concentrated: DIA is in 48 of the 277 and in 4 of the 5 nominees, so the nominees are not 5 independent findings. Statistical cointegration in S is not tradeability (cf. the 09-29 pairs P&L-artifact audit). Nothing is claimable; confirmation would need owner-registered hypotheses (D-3, at most 5) tested in zone V (Holm, B = 999) under the corrected pairs P&L/cost model.


---

## 2026-10-07 — CRYPTO SCREEN: ENGLE-GRANGER SIZE CHECKS (DESCRIPTIVE)

- Question: is the screen's 32.8 % raw EG rejection rate a test artifact? Same function and settings as the screen (bot.quant_core.cointegration.engle_granger, maxlag=None, autolag='aic', log prices, n = 10,000 = the screen's training window).
- (1) Synthetic nulls, existing tools (bot.quant_core.calibration.eg_size_run; 200 replicates per cell, no cointegration by construction). Size at 5 %:
  - independent random walks 3.5 / 5.0 / 9.0 % (gaussian / student-t3 / GARCH);
  - common factor 2.5 / 8.5 / 4.5 %;
  - beta walk 5.0 / 4.0 / 5.5 %;
  - worst Wilson upper bound 13.8 %.
  Heavy tails and volatility clustering inflate size modestly, nowhere near 33 %.
- (2) Real-data null: 1,000 random pairs of 149 coins (6 excluded by the screen's 3-candle gap rule), zone S only.
  - Same period (the screen's setting): 323/1000 = 32.3 % [29.5, 35.3], which reproduces the screen.
  - B taken from a non-overlapping later part of S (no shared calendar time; real jumps, breaks and flat stretches kept): 4/1000 = 0.4 % [0.2, 1.0].
  - The rejections therefore require contemporaneous co-movement; data quirks do not produce them.
- (3) Flat (zero-change) hours in the first 10k of S: median coin 3.7 %; DIA 10.7 % (rank 13/149); top PAXG 20.5 %. Flat-heavy coins are inside the shifted null, which barely rejects, so flatness does not explain DIA's survivor concentration.
- Reading: the zone-S signal is statistically genuine IN-SAMPLE (coins behave as a market trend plus a mean-reverting idiosyncratic part, Nov 2021 - Dec 2022). Not established: persistence out of sample (zone V) or tradeability after costs. Still EXPLORATORY; no claim. Scripts and results: session scratchpad (eg_size_crypto.py, eg_realdata_null.py, *_result.json).


---

## 2026-10-07 — V-1 RESOLVED (STRICT): ZONE V IS NOT INDEPENDENT OF THE BACKTESTER STRATEGIES

- Question (Revision B §11): were the MovingAverage / MeanReversion parameters and the router thresholds set while zone V data (2024-04-22 onward) was visible?
- Evidence (git): repository first commit 2026-08-16. moving_average.py, mean_reversion_strategy.py and regime_detector.py were first and last committed 2026-08-16 (1 commit each). strategy_router.py was first committed 2026-08-16 and last changed 2026-08-20; min_adx_for_trend and the backtester thresholds date from the initial commit. All of this is more than two years after V0, with most of V already observable. Design work from before the repository existed cannot be shown either way, so independence cannot be established.
- Ruling (strictest, per the delegated-strictness rule): zone V is NOT independent of these strategies' design. Journal findings about StrategyRouter[MovingAverageStrategy, MeanReversionStrategy] can never be confirmatory in V; only the sealed holdout F (from 2026-10-04) is clean for them. Enforced in code by the Phase 4 journal read guard.


---

## 2026-10-07 — DECISION JOURNAL PHASES 2-7 IMPLEMENTED (INFRASTRUCTURE, NOT EVIDENCE)

- Commits: Phase 2 outcome labels / post-trade metrics 4f3c8cf; Phase 3 legacy backfill 5db832a; Phase 4 descriptive analytics + zone read guard + V-1 da7f40c; Phase 5 human annotations 3989c0a (+ a320653 marker restore); Phase 6 DRY_RUN live recorder d4578bb; Phase 7 statsmodels-logistic discovery 5a3c9f7. Migrations 0017-0020, all additive (two constraints relaxed only for clearly-marked LEGACY_BACKFILL and DRY_RUN/LIVE rows), applied locally.
- Phase 6 (owner blocker test): the real bot_runner.process_venue_candle and the real ExecutionEngine (DRY_RUN) were driven offline over 400 replayed hours with the recorder OFF and then ON. Statuses, signals, regimes, every TradeRecord field and the engine state were identical; negative controls were detected. The full regression then caught a recorder-OFF engine read (three hooks evaluated engine attributes even with no recorder attached). It was fixed before commit with lazy hook arguments and pinned by RecorderOffPathTests / mutation P6-7. The recorder is off unless JOURNAL_LIVE_RECORDER=1 and refuses non-DRY_RUN engines. Live and dry-run outcomes are never labelled (sealed until the D-8 evaluation, OD-J4).
- Phase 7: zone-S episodes only, purged + embargoed walk-forward folds, out-of-fold log-loss vs base rate and AUC, sign stability >= 0.8. Output is candidate hypotheses for owner review only: nothing is registered and nothing is reachable from trading code (import-boundary test). Evidence level is at most OBSERVATION. No scikit-learn.
- Tests: 86 new across Phases 2-7; 32/32 mutations caught (P2-8, P4-3 and P7-4 exposed test holes, all fixed). Final offline regression after the last edit: 130 journal tests OK. The 6 remaining failures were all pre-existing and were reproduced identically on HEAD for the live-path modules (3 bot_runner venue-session network tests, test_exit_updates_trade_record, forex currency balance, research-lab 0.336 baseline).
- No confirmatory journal test was run: none is permitted in zone V for the backtester strategies (V-1), and F is sealed. No strategy, execution, risk or live-trading behaviour changed. Report: TRADING_DECISION_JOURNAL_PHASES_2-7_REPORT_2026-10-07.md.
