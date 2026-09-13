# Regime-Conditional Quantitative Research — Architecture Report

Companion to `REGIME_AWARE_RESEARCH_IMPLEMENTATION_REPORT.md` (files touched,
tests, real validation-run numbers, PASS/FAIL). This report covers the
forensic findings, the taxonomy design, and every methodology decision —
the "how and why," reviewed and approved before implementation began, per
the mission's own stop-condition requirement.

## 1. Root cause of the "regime detection breakdown"

`bot/research_lab/verdict.py:47-52`. The Research Lab's deterministic
verdict taxonomy (`bot/research_lab/models.py:61-71`) has always had nine
formally defined states, including `REGIME_DEPENDENT`. `compute_verdict()`'s
own docstring says, verbatim: *"REGIME_DEPENDENT and
SUPERSEDED_BY_EXISTING_RESEARCH are real states in the taxonomy but this
MVP pass has no regime-stratified tool output... rather than fabricating a
path to them here."* `REGIME_DEPENDENT` was a **defined but structurally
unreachable** verdict state — an honestly-documented gap, not a silent
failure. This mission closes that gap with a real, separate, regime-aware
status function (§6 below) rather than retrofitting the unreachable state
directly, for reasons explained there.

## 2. Existing regime infrastructure — four surfaces, none directly reusable as-is

Four independent "regime" surfaces existed before this mission, none of
which the mission could extend directly:

1. **`bot/engines/regime_detector.py`'s `RegimeDetector`** — the live/backtest
   engine (`TRENDING_UP/TRENDING_DOWN/RANGING/HIGH_VOLATILITY`). Every
   indicator is causal (trailing `.rolling()`/`.ewm()` only), but it is a
   **point-in-time classifier** — `detect(df)` returns one result for `df`'s
   last row. Producing a full historical label series by calling it once
   per timestamp is O(n²).
2. **`bot/backtesting/regime_precomputer.py`'s `precompute_regime_results()`**
   — found during this mission's forensic pass (missed in the initial
   `bot/research/`-only sweep; surfaced while checking `bot/tests/` for
   existing regime coverage). Already solves the O(n²) problem: it
   vectorizes all four `RegimeDetector` indicator formulas in one pass, with
   its own dedicated test (`RegimePrecomputerEquivalenceTest`) proving
   byte-identical output to the live per-candle path. Its own docstring
   warns that a second independent reimplementation would risk drift from
   production math.
3. **`bot/research/feature_validator.py`'s `MarketRegimeDetector`** —
   vectorized (O(n)), but a different taxonomy (`CRASH/RECOVERY/TRENDING/
   VOLATILE/RANGING`, no up/down split). Already computes a regime-
   conditional IC internally, but the result never leaves the function.
4. **`bot/research/feature_decay_analyzer.py`'s `REGIME_WINDOWS`** — not a
   classifier at all: hand-typed calendar date ranges tied to crypto market
   narrative (`"luna_crash"`, `"ftx_crash"`, ...). Meaningless for Forex, and
   silently drops any window with too few observations via a bare
   `continue` — no `INSUFFICIENT_SAMPLE` marker. This is very likely the
   concrete failure mode behind "a regime breakdown report comes back
   sparse with no explanation."

Confirmed independently by `FOREX_PORTABILITY_MATRIX.md` (an earlier
session's deliverable): *"regime classification exists only in
bot.engines.regime_detector (live execution, unrelated to research) and
feature_validator.py's MarketRegimeDetector (a labeling utility, not a
standalone engine)."*

Grepped and confirmed **zero** regime references in: `oos_validator.py`,
`walk_forward_engine.py`, `permutation_test_engine.py`,
`cointegration_engine.py`, `kalman_filter_engine.py`, `cross_section_engine.py`,
`cross_sectional_permutation_test.py`, `bot/research_lab/orchestrator.py`,
`evidence_interface.py`, `bot/research/capabilities.py`.

## 3. What was reused vs. built new

**Reused, unmodified:** `RegimeDetector`'s indicator formulas (via
`regime_precomputer.py`, not re-derived), `cointegration_engine.py`'s
`_test_pair()`, `kalman_filter_engine.py`'s `run_on_prices()`,
`oos_validator.py`'s fold/purge/embargo machinery and `FoldEvalResult.trades`
(already timestamped per-period records — required zero core changes),
`cross_sectional_permutation_test.py`'s `_within_timestamp_shuffle()`,
`permutation_stats.py`'s `compute_permutation_verdict()`/`is_significant()`,
`statsmodels`' FDR correction (`multipletests`, Benjamini-Hochberg — same
method already used identically elsewhere in this codebase).

**Built new:** a canonical regime taxonomy and labeling module
(`bot/research/regime_labels.py`) and five regime-conditional analysis
modules, each a thin "slice by regime, call the existing method" wrapper
— never a reimplementation of the underlying statistics. Full file list in
the implementation report.

## 4. Regime taxonomy (Phase 2)

Two independent dimensions, versioned (`REGIME_TAXONOMY_VERSION = "1.0.0"`):

| Dimension | States |
|---|---|
| `trend_state` | `TRENDING_UP` \| `TRENDING_DOWN` \| `RANGING` |
| `volatility_state` | `LOW_VOLATILITY` \| `NORMAL_VOLATILITY` \| `HIGH_VOLATILITY` |
| `regime_label` (their cross) | 9 combined cells |

- **Input features:** ADX (trend strength) and ATR-vs-trailing-baseline
  ratio (volatility), sourced from `regime_precomputer.py` — the tested,
  production-exact values, not re-derived. EMA fast/slow (trend direction)
  computed directly (a single low-risk `.ewm().mean()` line, not the
  multi-step Wilder-smoothing math the drift-risk warning is about).
- **Lookback:** ADX period 14, EMA 20/50, ATR period 14 vs. a 50-period
  baseline — identical to `RegimeDetector`'s defaults, for continuity, not
  re-tuned for either asset class (re-tuning risks curve-fitting regime
  boundaries to a specific result).
- **Why not reuse `RegimeDetector`'s own 4-state output directly:** that
  scheme is mutually exclusive — `HIGH_VOLATILITY` always overrides and
  replaces any trend classification, so `"TRENDING_UP + HIGH_VOLATILITY"`
  (a combination Phase 7 explicitly names) can never occur in it by
  construction. The mission's taxonomy needs trend and volatility as
  independent, combinable dimensions — a genuinely different shape, not a
  relabeling.
- **Parameters:** fixed, not learned. A future taxonomy version requires an
  explicit version bump, never a silent in-place edit.
- **Minimum sample:** 30 observations per regime cell (`MIN_REGIME_OBS`),
  reused from `feature_validator.py`'s own existing `>30` convention rather
  than inventing a second, drifting floor.
- **Warmup / missing labels:** rows before `min_periods` (60, matching
  `RegimeDetector.min_candles`) or with a genuine input gap get `NaN`,
  never a default regime — the mission's explicit "missing regime labels
  must never silently become a valid regime" rule.

## 5. Leakage controls

- **No look-ahead in the labels themselves:** every computation is a
  trailing `.rolling()`/`.ewm()`/`.shift(1)` — verified not just by
  inspection but by a real, automated **prefix-invariance test**
  (`assert_no_lookahead()`): computing labels on the full series and on a
  truncated prefix must produce byte-identical labels on the overlapping
  range. If a future row ever influenced a past label, truncating the
  future would change it — this is checked at multiple truncation points
  in `test_regime_labels.py`, not asserted once and trusted.
- **OOS fold integrity:** regime labels are computed **once**, on the full
  causal history, entirely outside and before any fold is built. The
  regime-conditional OOS/permutation modules only ever *slice* an
  already-built, already-purged/embargoed `OOSResult`'s fold-level records
  by timestamp — they never re-derive fold boundaries, never touch
  `purge_periods`/`embargo_periods`, and a dedicated test
  (`test_fold_specs_are_untouched_by_regime_conditioning`) asserts the
  `FoldSpec` objects are identical before and after regime-conditioning.
- **Cointegration episode contiguity:** naively concatenating every row
  sharing a regime label — even from non-adjacent time windows — would
  splice unrelated periods together and manufacture artificial
  discontinuities at each splice boundary, biasing the ADF test. Regime-
  conditional cointegration therefore tests only **contiguous** regime
  episodes (`label_regime_episodes()`), each run through the engine's
  unmodified `_test_pair()` independently, never a Frankenstein-spliced
  series.
- **Kalman is handled differently, deliberately:** a Kalman filter's state
  evolves recursively and must run over one continuous series to mean
  anything — segmenting it per episode would discard exactly the cross-
  episode continuity that makes a dynamic hedge ratio interesting. So
  Kalman runs **once**, unmodified, over the full aligned series (already
  proven causal — `kalman_beta_pred` is explicitly the leakage-free,
  pre-update estimate), and its *output* is grouped by regime label post
  hoc — safe, because grouping an already-computed, already-causal
  per-timestamp value by an independently causal per-timestamp label
  introduces no new information at any point.

## 6. IC / p-value / FDR methodology (Phases 7–9)

For each regime cell (overall + every state in every requested taxonomy
dimension), Spearman IC and p-value are computed directly
(`scipy.stats.spearmanr`) subject to the `MIN_REGIME_OBS` floor. Stability
(Phase 8) uses **block IC**, not a rolling window: each regime's own
chronological subsequence is split into ≤10 contiguous blocks, one IC per
block. This is O(n) rather than a rolling-window `.apply()`'s O(n·window)
— a real, deliberate Phase 20 performance choice — and answers the same
question ("is the IC consistently positive, not just positive on average")
via mean/median/std/positive-fraction/95%-CI of the block ICs.

**FDR family, defined explicitly (Phase 9):** for one `(feature_col,
forward_return_col)` pair, the family is every regime cell across every
requested dimension (trend alone ≤3, volatility alone ≤3, the combined
cross ≤9) **plus** the overall row, with sufficient sample — corrected
together, once, via Benjamini-Hochberg (`statsmodels.stats.multitest.
multipletests`), the same method this codebase already uses identically in
`cointegration_engine.py` and `cross_sectional_permutation_test.py`. A
cell testing multiple *different* features/horizons must call this
function once per feature/horizon and treat each as its own family — this
module does not, and should not, silently pool unrelated features into
one family (that would need an orchestrator-level policy decision; the
separately-documented, still-open gap "no FDR correction across multiple
`hypothesis_name` choices" is not solved here).

**Interaction test (Phase 10):** a direct, standard nested-model F-test
(`statsmodels`' `anova_lm`, comparing `return ~ feature + C(regime)` against
`return ~ feature * C(regime)`) — not separate per-regime significance
flags interpreted as proof regimes differ. Verified on synthetic data with
a planted regime-dependent effect (interaction fires, p < 0.01) and a
negative control with a regime-*independent* effect (interaction does not
fire).

## 7. Cointegration / hedge-ratio / Kalman methodology (Phases 4–6)

Full-sample cointegration is reproduced byte-identically (verified by
test) alongside per-episode results. An episode below the engine's own
`MIN_CANDLES` (1,000 — **not lowered**) is marked `TOO_SHORT`, never
silently tested or silently dropped; a regime with zero qualifying
episodes is reported `INSUFFICIENT_SAMPLE` with the reason stated, not
omitted. Static hedge-ratio dispersion (mean/std/min/max) is aggregated
across qualifying episodes per regime. Kalman's post-hoc regime bucketing
reports a `cross_regime_instability_ratio` (the spread of per-regime *means*
relative to full-sample std) — a direct, real answer to "is the hedge
ratio stable across regimes, or does the relationship structurally
change."

## 8. OOS / permutation / cost methodology (Phases 11–13)

**OOS-by-regime** consumes an unmodified `OOSResult`'s `folds[i].trades`
(already timestamped per-period records) and recomputes the *exact same*
economic-metrics formula (`oos_validator.compute_cross_sectional_metrics_
from_records` — a new, purely-additive public wrapper around the existing
private function, zero logic change) on each regime's subset. Phase 13's
economic-significance numbers (mean net return, Sharpe, hit rate,
drawdown, cost, turnover) fall out of this directly — no separate economic
engine was built.

**Permutation-by-regime:** audited first, per Phase 12's own instruction.
Finding: the existing within-timestamp shuffle already preserves exactly
the right structure for a regime-conditional null (a timestamp's regime
label never depends on which asset a feature value is attached to), so no
new shuffling logic was needed. What was missing was purely a
memory-vs-completeness tradeoff — the existing sweep wrapper deliberately
discards each replica's full result to bound memory, so a new
orchestration function (reusing every existing primitive) was written to
retain regime-sliced metrics per replica. Null hypothesis, what's permuted,
and what's preserved are all identical to the existing cross-sectional
test's own documented methodology — this module changes nothing about the
shuffle, only what is measured afterward.

**Cost methodology:** every regime-conditional module accepts `cost_rate`
as an explicit parameter, passed by the caller — no new cost assumption
was invented, and (see implementation report) the real validation run
confirmed why this must be the *correct*, asset-class-specific rate
(Forex's real ~1bp pip-based cost, not crypto's flat 15bp placeholder)
rather than a hardcoded default.

## 9. What was deliberately NOT done

- `compute_verdict()` (the existing single-hypothesis, whole-dataset
  verdict engine) was **not modified**. Its `REGIME_DEPENDENT`/
  `SUPERSEDED_BY_EXISTING_RESEARCH` states remain exactly as honestly
  documented — still unreachable through that function. This mission's
  Phase 18 status function (`regime_conditional_status.py`) is a
  **deliberately separate** taxonomy answering a different question
  ("under which regime does this hold up, and how far does the evidence
  go") with its own nine-state vocabulary. Wiring the two together (e.g.
  having a real `ResearchExperiment.verdict` ever read `REGIME_DEPENDENT`)
  is a genuine, separate orchestrator/data-model decision, named here as
  an open item rather than made unilaterally.
- No regime taxonomy parameter was tuned against any result — every
  threshold matches `RegimeDetector`'s pre-existing, already-in-production
  defaults.
- No statistical gate (FDR alpha, `MIN_CANDLES`, `MIN_REGIME_OBS`,
  cointegration threshold) was loosened anywhere.
- No ML strategy selection, no live-trading signal, no strategy
  optimization — every module here answers "does a relationship exist and
  under which regime," never "what parameters make it profitable."
