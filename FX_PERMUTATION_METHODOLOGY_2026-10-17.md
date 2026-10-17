# FX DCA Permutation Test — Methodology Specification M2

**Identifier:** FX-PERM-METHODOLOGY-M2
**Date:** 2026-10-17
**Status:** LOCKED DESIGN — written before calibration test results were read.
  The synthetic calibration tests (Test 1: under-null type I error; Test 2:
  block-size sensitivity) were launched before this document was finalised.
  Their results were not yet available when Section 3 (calibration plan) was
  written. This document must be committed before the calibration output file
  is read.

**Governance note — post-hoc status:**
  M1 (the original methodology, implemented in `bot/research/fx_permutation_test.py`)
  was never formally described in a standalone specification document. The
  v3/A3 protocol (Part 5) specified the high-level design but not the
  implementation details audited here. M2 is a RETROSPECTIVE AUDIT and DESIGN
  CORRECTION. The following is NOT a re-description of M1 as if pre-specified;
  it is a new locked design that supersedes M1 going forward. All v5/A4 and
  earlier results were produced under M1 and are labelled accordingly.

**Holdout:** SEALED throughout all M1 and M2 work.

---

## Part 1 — Formal Statistical Specification

### 1.1 Null hypothesis H0

> The strategy's total realised P&L, accumulated across all walk-forward test
> folds, is no greater than what would be expected if the temporal ordering of
> the OHLCV candles within each test fold were randomised — specifically, if
> the candles were permuted in contiguous blocks of BLOCK_SIZE candles, with
> all candles within each block kept in their original within-block order.

Formally: let T(y) be the test statistic evaluated on the real price sequence y,
and let T(y*_b) be the same statistic evaluated on a block-permuted replica y*_b.
H0: T(y) is drawn from the same distribution as T(y*_b).
H1 (one-sided): T(y) > T(y*_b) in distribution — the real temporal ordering
produces systematically higher P&L than block-permuted orderings.

**What the null destroys:** temporal autocorrelation operating at frequencies
longer than BLOCK_SIZE candles (multi-day trends, regime persistence). It
preserves within-block autocorrelation (e.g. intraday mean-reversion, micro
price dynamics within 48h).

**What the null does NOT destroy:** the overall price level distribution, the
within-block return dynamics, the cost model applied to each simulated trade,
and the per-fold ladder spec (derived from real training data; held fixed
across all permutations — this is intentional: the spec represents a prior
learned before the test period).

### 1.2 Test statistic (primary)

`T = total_realised_pnl` = sum of `profit_usdt` across all closed trades in
all test folds combined, where:
- "closed" = `exit_reason != "DATA_END_STILL_OPEN"`
- `profit_usdt` = gross P&L + swap P&L − commission (cost-adjusted)
- Under v5/A4: TIME_EXIT trades are closed trades; they ARE included

Rationale: this is the quantity with direct economic meaning. Win rate is not
a discriminating statistic here (every trade wins or time-exits; there is no
stop-loss). The sum aggregates across folds so a strategy that wins in folds
1–2 and loses in folds 3–5 is not rewarded.

**Secondary statistic:** `final_equity_sum` = sum of per-fold final equity
(realised + unrealised MTM at test-period end). Reported alongside primary;
not used in any significance decision.

### 1.3 Block construction

**Algorithm:**
1. Take the test fold DataFrame (n rows) with its original DatetimeIndex.
2. Divide into `ceil(n / BLOCK_SIZE)` contiguous blocks:
   block_k = rows [k×B, min((k+1)×B, n)) for k = 0, 1, …
3. Draw a uniform random permutation of the block indices.
4. Concatenate the blocks in the permuted order.
5. The DatetimeIndex is NOT shuffled — it retains the original chronological
   positions. Only the OHLCV content moves.

**Known limitation — last-block truncation:**
The final block may contain as few as 1 candle (when n mod BLOCK_SIZE = 1).
This truncated block can land at any position in the shuffled sequence,
creating a single-candle "block" surrounded by full 48-candle blocks. This is
a minor asymmetry: the truncated block's content has less opportunity to
trigger a DCA level or exit than a full block. The bias direction is unclear
(it could reduce or increase permuted P&L). This limitation is DOCUMENTED
here and accepted for M2; a circular block bootstrap (which eliminates
truncation) is a candidate for M3.

**Known limitation — block-boundary price gaps:**
Because raw OHLCV price levels (not log-returns) are shuffled, consecutive
blocks in a permuted sequence may have a price gap at their boundary (the
opening price of block k+1 differs from the closing price of block k). These
gaps are not present in real data. Their effect on the test statistic is
assessed in Test 2 (block-size sensitivity) and Test 1 (under-null calibration):
if the test returns p≈1.000 on iid random-walk data where H0 is known to hold,
this would indicate the block-gap artefact dominates and the test is
anti-conservative. Results of those tests are recorded in Part 3.

### 1.4 Block size

**Chosen value:** BLOCK_SIZE = 48 candles (48 hours on 1h data).

**Rationale (M1, unchanged for M2):** the strategy's average hold time
(from entry to exit) substantially exceeds BLOCK_SIZE, so blocks within a
typical cycle are themselves permuted, preserving short-range price dynamics
while destroying the multi-week trend persistence that the strategy is
hypothesised to be sensitive to. With average TIME_EXIT hold = 720 candles,
a 48h block is approximately 1/15 of a typical adverse cycle.

**Sensitivity:** see Test 2 (block sizes 12, 24, 48, 96, 192). If p-values
across block sizes all remain >> 0.05 (or all << 0.05), the result is
robust to this choice. If p varies dramatically, the chosen value is
driving the result and must be re-examined.

### 1.5 Number of permutations

**N = 499** (production). Smallest achievable p = 1/(499+1) = 0.0020.
This resolves to two decimal places (Phipson & Smyth 2010 "+1 correction"):

  p = (number of permuted values ≥ real value + 1) / (N + 1)

Under the Bonferroni thresholds in Part 1.7, rejection requires p < α. With
N=499 and α=0.0167 (v6, 3 tests), rejection requires ≤ 8 of 499 shuffles to
beat the real value. Achievable.

Diagnostic (calibration and sensitivity) runs: N=29. Smallest p = 1/30 ≈
0.033. Sufficient for checking the shape of the null distribution; not
sufficient for production inference.

### 1.6 Hypothesis family (M2, updated from M1)

**M1 family (v5/A4, 10 tests):** 5 pairs × 2 directions = 10 primary tests.
Bonferroni α_per_test = 0.01 (hardcoded as `BONFERRONI_ALPHA_PER_PAIR` in
`fx_permutation_test.py`). Note: the constant is named "per pair" but applied
per direction — it was set as 0.05/5 = 0.01, effectively treating the family
as 5 pairs (not 10 direction-tests). This is documented as an M1 ambiguity.
Family-wise α = 0.05 under this interpretation.

**M2 family (v6/A5, 3 tests):** carry gate restricts to 3 eligible directions
(EUR/USD SHORT, USD/JPY LONG, USD/CHF LONG). Family = 3 tests.
Bonferroni α_per_test = 0.05/3 = **0.0167**.

**M2 code fix required:** `BONFERRONI_ALPHA_PER_PAIR = 0.01` is hardcoded
and wrong for the 3-test family. Must be made a parameter of
`run_fx_calibration_permutation()` and `print_permutation_verdict()`.
Existing default (0.01) is kept for backward compatibility with M1 calls;
v6 calls must explicitly pass `bonferroni_alpha=0.0167`.

### 1.7 Multiple-testing correction

**Primary: Bonferroni**
Most conservative. Rejects H0_i if p_i < α_family / m, where m is the
number of tests in the family and α_family = 0.05.
M2/v6: α_per_test = 0.05/3 = 0.0167.

**Secondary: FDR-BH (Benjamini-Hochberg)**
Applied per-pair (not per direction), using max(p_LONG, p_SHORT) as the
per-pair p-value in M1; for M2/v6 there is no (LONG, SHORT) split within
the family (each eligible direction appears once), so BH is applied directly
to the 3 direction p-values.

Implementation in `compute_fdr_bh()`: step-UP procedure (find largest k such
that p_(k) ≤ α·k/m; reject all ranks ≤ k). The docstring incorrectly says
"step-down"; the logic is correct. This is an M2 documentation fix.

**Stouffer combination:** INDICATIVE ONLY — folds are not independent
(earlier folds' test data appears in later folds' training data). Must not
be used as a primary gate. Correctly labelled in existing code.

### 1.8 Decision thresholds

| Gate | Threshold | Family | Action |
|------|-----------|--------|--------|
| Primary (Bonferroni) | p < 0.0167 | 3 tests (M2/v6) | Reject H0 for that direction |
| Secondary (FDR-BH) | BH reject = True | 3 directions, α=0.05 | Corroborating evidence |
| Stage gate | All primary p < 0.0167 | — | Advance to zone V holdout |

A single significant result (1/3) is not sufficient for the stage gate. All
three eligible directions must survive Bonferroni at M2 thresholds before
zone V access is authorised.

---

## Part 2 — Audit Findings vs M1

The following gaps and issues were identified in M1 (`fx_permutation_test.py`)
during the 2026-10-17 audit. They are listed as FINDINGS; the corrections are
described in Part 3 (calibration design) and Part 4 (code fixes).

**F-M2-1 (Critical): Bonferroni constant hardcoded for 5 pairs.**
`BONFERRONI_ALPHA_PER_PAIR = 0.01` is a module-level constant. It was correct
for the 10-test M1 family (per-pair interpretation) but is wrong for the
3-test M2/v6 family (α=0.0167). Any v6 call using the default will apply the
wrong threshold. Fix: parameterize — see Part 4.

**F-M2-2 (Critical): `max_cycle_candles` not explicit in permuted fold.**
`_run_one_permuted_fold()` calls `_simulate_data_derived_next_open()` without
passing `max_cycle_candles`. The value used is the module-level default of
`fx_fold_runner.MAX_CYCLE_CANDLES`. If that default changes, the permutation
null distribution changes silently without any indication in the permutation
test code. Fix: pass explicitly — see Part 4.

**F-M2-3 (Moderate): No calibration test has ever been run.**
Under H0, p-values should be approximately uniform. This was never verified
on synthetic data. Fix: see Part 3 (Test 1).

**F-M2-4 (Moderate): Block-size sensitivity never tested.**
The 48h block size was chosen by reasoning, not empirically validated.
Fix: see Part 3 (Test 2).

**F-M2-5 (Minor): Last-block truncation.**
The final block in each shuffle may be shorter than BLOCK_SIZE. Documented
in Section 1.3; accepted for M2.

**F-M2-6 (Minor): BH docstring says "step-down", procedure is step-up.**
The logic is correct; the comment is wrong. Fix: update comment.

**F-M2-7 (Minor): Null hypothesis never stated as a single sentence.**
The module docstring describes the mechanic but not a formal H0/H1. Fix:
add Part 1 of this document and reference it from the module docstring.

---

## Part 3 — Calibration Plan and Results

*(Written before calibration results were read. Results filled in after
`permutation_audit_tests.py` completes.)*

### Test 1: Under-null type I error rate

**Design:**
- K=20 independent iid random-walk price series (GBM, μ=0, σ=0.0005/h,
  37,000 candles each, starting price 1.1000)
- Each split into calibration/holdout via `make_fx_splits()` (same splitter)
- For each series: run fold simulation (LONG, zero-cost spec) then permutation
  test with N=29, block=48
- Collect 20 p-values

**Expected under H0:** p-values approximately uniform over {1/30, 2/30, ..., 1}.
Expected fraction with p < 0.05: ≈1/20 = 5%.

**Threshold for concern:** if ≥15/20 runs give p=1.000 (real beaten by all
shuffles), the test is anti-conservative under iid data — the block-gap
artefact is dominating and the test statistic distribution is biased against
the real temporal sequence even when no edge exists. In that case, the
production results (p=1.000) cannot be interpreted as evidence against edge.

**Result:** *(filled after script completes)*

### Test 2: Block-size sensitivity

**Design:**
- Real USD/JPY LONG data (production dataset)
- N=29 permutations per block size
- Block sizes: [12, 24, 48, 96, 192] candles
- Record overall_p_value and overall_percentile for each

**Expected if result is genuine:** p >> 0.05 across all block sizes.
**Expected if block size is driving the result:** p varies across the range.

**Result:** *(filled after script completes)*

---

## Part 4 — Code Fixes Required Before M2 Production Run

The following changes to `fx_permutation_test.py` must be made and tested
before running the v6 production permutation test:

### Fix F-M2-1: Parameterize Bonferroni α

Add `bonferroni_alpha: float` parameter to `run_fx_calibration_permutation()`
and `print_permutation_verdict()`. Default = `BONFERRONI_ALPHA_PER_PAIR`
(0.01) for backward compatibility with any M1 callers. v6 callers must pass
`bonferroni_alpha=0.05/3`.

### Fix F-M2-2: Explicit `max_cycle_candles` in permuted fold

Change `_run_one_permuted_fold()` to accept `max_cycle_candles` as an
explicit parameter and pass it to `_simulate_data_derived_next_open()`.
Change `run_fx_calibration_permutation()` to accept and forward
`max_cycle_candles`, defaulting to `MAX_CYCLE_CANDLES` from `fx_fold_runner`.

### Fix F-M2-6: BH docstring

Change "step-down" to "step-up" in `compute_fdr_bh()` docstring.

---

## Part 5 — Authorised Production Experiment (M2)

Once:
1. This document is committed (before reading calibration results)
2. Code fixes F-M2-1 and F-M2-2 are implemented and tested
3. Calibration results (Tests 1 and 2) are read and recorded in Part 3
4. No disqualifying calibration failure is found (see Part 3 thresholds)

The following production experiment is authorised:

- **Pairs/directions:** EUR/USD SHORT, USD/JPY LONG, USD/CHF LONG (v6/A5 carry gate)
- **Parameters:** v6/A5 carry gate + ADX(14)≤25 entry gate + all v5/A4 parameters
- **N:** 499 permutations
- **Block size:** 48h
- **Bonferroni α:** 0.05/3 = 0.0167 per test
- **Seed:** 20261017
- **Holdout:** SEALED throughout

These results, produced after this document is committed and the code fixes are
applied, constitute the M2 primary evidence for or against the v6/A5 strategy.

---

## Governance

**This document supersedes:** the informal methodology implicit in M1
(bot/research/fx_permutation_test.py docstring + protocol v3/A3 Part 5).

**M1 results status:** All v4/A3 and v5/A4 permutation results are labelled
M1. They remain on record. They were produced with the methodology described
in the module docstring of fx_permutation_test.py and the limitations
documented in Part 2. Their headline conclusion (p=1.000, no detectable edge
in zone S under M1) stands, but cannot be compared directly to M2 results
because the Bonferroni threshold and the explicit max_cycle_candles parameter
may change the significance assessment.

**Hash:** SHA-256 of this document to be recorded in the audit log after
calibration results are filled in and the document is finalised.
