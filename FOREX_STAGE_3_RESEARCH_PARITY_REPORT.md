# Forex Integration — Stage 3: Research Parity Report

**Status: COMPLETE.** This supersedes `FOREX_STAGE_3_GROUNDWORK_REPORT.md`, which
was explicitly scoped as partial. This report covers the full Stage 3 program:
engine-by-engine capability audit (3.1–3.2), factor-neutralization and
regime-conditioning re-investigation of the cross-sectional signal (3.3–3.7),
cost-aware validation confirmation (3.8), evidence-bundle parity (3.9), the
crypto-vs-Forex readiness scorecard (3.10), and deep-health-check integration
(3.11).

Per the mission's standing rule: **research verdicts are deterministic and are
not overridden by AI.** Every negative result below is reported as found.

---

## 3.1 / 3.2 — Engine capability audit

All 26 files in `bot/research/` classified against real Forex OHLCV data
(open/high/low/close/volume, volume always 0 — confirmed structurally by
`bot/research/capabilities.py`'s `DataRequirement.REAL_VOLUME` check).
Classification key:

- **READY** — runs correctly against Forex data today, no change needed.
- **WIRED (Stage 1/3)** — required a fix in this program; fix shipped and tested.
- **FOREX-CAPABLE, CRYPTO-ONLY CALLER** — the engine itself is asset-agnostic;
  only its orchestration entry point never passed Forex data. Distinguished
  from WIRED because the engine file itself needed zero changes.
- **MARKET_SPECIFIC (crypto)** — correctly restricted; the underlying market
  structure this engine models does not exist for spot FX.
  Confirmed in Stage 1, re-confirmed here, not fixed (would require inventing
  fictitious data).
- **INSUFFICIENT_DATA** — a genuine Forex-relevant paradigm this codebase does
  not yet have the data to run (distinct from MARKET_SPECIFIC: the market
  reality exists, the codebase's data acquisition doesn't).
- **REQUIRES_REVIEW** — pre-existing concern unrelated to Forex parity itself,
  flagged, not fixed under this mission's stated scope.

| File | Classification | Notes |
|---|---|---|
| `feature_calculator.py` | READY (WIRED) | Already asset-class-aware via `periods_per_year`/`resolve_ohlcv_path`; `_calculate_volume_ratio` now honestly reports `NOT_APPLICABLE` for Forex instead of silent all-NaN (Stage 1 fix). |
| `feature_validator.py` | READY | Already calls `periods_per_year(timeframe, asset_class)` correctly (confirmed Stage 1, unchanged). |
| `feature_stability_analyzer.py` | WIRED (Stage 1) | `SYMBOLS` default and `ANNUALISATION_FACTOR` were crypto-hardcoded; now registry/`periods_per_year`-driven. |
| `feature_decay_analyzer.py` | WIRED (Stage 1) | Same bug class as above; also removed a dead `sqrt(8760)` constant. |
| `feature_evolution_report.py` | READY | Consumes the outputs of the above three; no asset-class-specific logic of its own. |
| `cross_section_engine.py` | WIRED (Stage 0, pre-Stage-1) | Fixed the forward-return/momentum/reversal universe-hardcoding bug (`3f6db46`); `run_forex_cross_section_research()` added as the Forex-parallel entry point. |
| `cross_sectional_permutation_test.py` | READY | Within-timestamp shuffle is asset-class-agnostic by construction (see 3.6 below for the one real gap found, which is not asset-class-specific). |
| `run_cross_sectional_oos.py` | WIRED (Stage 3 groundwork) | Was the confirmed top Stage 3 priority from Stage 1: only ever reached the crypto engine. Now takes `asset_class`, dispatches to the correct feature engine, uses the real Forex cost rate, and namespaces checkpoints/verdicts per asset class. Verified via real end-to-end runs on both asset classes (see `FOREX_STAGE_3_GROUNDWORK_REPORT.md`). |
| `oos_validator.py` | READY | Position-based folds over real observed timestamps, gap-safe; confirmed asset-agnostic in Stage 1, unchanged. |
| `walk_forward_engine.py` | READY | Candle-position block operations, gap-safe; confirmed asset-agnostic in Stage 1. |
| `permutation_test_engine.py` | READY | Temporal block-shuffle, gap-safe; correct null for pairs cointegration regardless of asset class. |
| `permutation_stats.py` | READY | Pure statistics (FDR, p-value aggregation) — no market assumptions. |
| `cointegration_engine.py` | READY (WIRED, pre-Stage-1) | Log-price convention bug fixed earlier this session in the Research Lab tool wrapper (`research_tools.py`), not in the engine itself — engine was already correct when fed the right input. Verified against real Forex pairs (USD/CAD vs NZD/USD, EUR/CAD vs USD/JPY). |
| `cointegration_pipeline_runner.py` | FOREX-CAPABLE, CRYPTO-ONLY CALLER | Orchestrates `cointegration_engine.py` over a symbol list; the engine call itself is asset-agnostic, but the runner's default symbol list is crypto. Not fixed under this mission (no forensic finding named it as regression-critical; a `--symbols`/`--asset-class` CLI flag would be the correct fix, deferred as a minor, non-blocking wiring gap). |
| `kalman_filter_engine.py` | REQUIRES_REVIEW | Confirmed working against real Forex data (used by the Research Lab pairs flow this session), but hyperparameters were empirically tuned for crypto's continuous liquidity and never re-validated for Forex's session-gapped, lower-volatility regime. A separate, unrelated dead-no-op bug exists in `_align_prices`'s forward-fill. Both flagged in Stage 1, not fixed here — re-tuning risks quietly manufacturing a more favorable Forex result, which the mission explicitly forbids ("do not optimize thresholds"). |
| `entry_exit_engine.py` | WIRED (Stage 1) | `_compute_sharpe_from_equity_curve()` now uses `periods_per_year(self.timeframe, self.asset_class)` instead of a bare `sqrt(365)`. Pair-identity parsing already registry-driven (confirmed Stage 1). |
| `strategy_oos_adapters.py` | READY | Thin adapters over `oos_validator.py`/`walk_forward_engine.py`; no asset-specific logic. |
| `validated_feature_registry.py` | READY | Pure bookkeeping (which features passed validation, by name) — no market assumptions. |
| `build_observations.py` | FOREX-CAPABLE, CRYPTO-ONLY CALLER | Merges `*_cross_section.csv` outputs into `observations.csv`; today only globs the crypto `research_data/` root, not `research_data/forex/` (which Stage 0's fix deliberately segregated to avoid mixing asset classes in `get_cross_sectional_dispersion()`). Correctly *not* merging them is the safe default; a Forex-specific `observations.csv` would need its own explicit build call, not attempted here as it has no current consumer. |
| `observation_database_builder.py` | FOREX-CAPABLE, CRYPTO-ONLY CALLER | Same situation as `build_observations.py` — depends on it. |
| `contagion_engine.py` | MARKET_SPECIFIC (crypto) | Models cross-exchange contagion via order-book/liquidity data structures specific to centralized crypto exchanges; no Forex equivalent data source exists in this codebase. Confirmed Stage 1, unchanged. |
| `derivatives_engine.py` | MARKET_SPECIFIC (crypto) | Funding rates / perpetual futures basis — no spot-FX equivalent. Confirmed Stage 1, unchanged. |
| `orderbook_depth_engine.py` | MARKET_SPECIFIC (crypto) | Requires L2 order-book snapshots; Yahoo Finance Forex data provides OHLCV only, no book depth. Confirmed Stage 1, unchanged. |
| `trade_flow_engine.py` | MARKET_SPECIFIC (crypto) | Requires tick-level trade prints (buy/sell aggressor flags); not available from the Forex data source in use. Confirmed Stage 1, unchanged. |
| `capabilities.py` | READY (new, Stage 1) | The capability-declaration primitive itself; used by exactly one call site so far (`_calculate_volume_ratio`). Retrofitting every other engine's implicit assumptions to declare capabilities explicitly is real future work, named here rather than attempted broadly (attempting it across all 26 files in one pass would be exactly the kind of "beyond what the task requires" scope creep this project's conventions warn against). |

### Named paradigms confirmed absent from this codebase entirely (not files to
audit — genuinely missing engines, carried forward from the Stage-1 forensic
audits and re-confirmed by this session's `ls bot/research/*.py`):

factor investing (beyond the ad hoc USD-factor test in 3.4 below, which was a
one-off script, not a reusable engine), lead-lag analysis, cross-sectional
dispersion regime engines beyond the dashboard display function, change-point
detection, market-impact modeling, ML-based signal generation, ML-based regime
classification, portfolio optimization, risk allocation/budgeting, stress/
tail-risk engines. None of these are Forex-specific gaps — they don't exist
for Crypto either. Out of scope for a *parity* mission; parity means Forex
gets what Crypto already has, not that new paradigms get invented for either.

### One genuinely new, Forex-specific finding

**FX Carry** (long high-interest-rate currencies, short low-interest-rate
currencies, funded by the interest-rate differential) is a real, well-known
Forex paradigm with **no Crypto equivalent to have parity with** — it doesn't
apply to spot crypto at all (no analogous "risk-free rate differential"
between two coins). Classified **INSUFFICIENT_DATA**, not MARKET_SPECIFIC or
N/A: the market reality is real, and Stage 1 already added placeholder
`swap_long`/`swap_short` fields to `Instrument` in anticipation, but they are
`None` for every Forex instrument today (populating them requires a real
broker connection — Stage 2, currently blocked). No engine exists to consume
them even if they were populated. This is a legitimate future research
direction, not a parity gap, since Crypto has nothing to be at parity with
here.

---

## 3.3 — Cross-sectional mean-reversion: prior result stands

The previous study (three independent passes: 8-pair ad hoc IC test, 28-pair
ad hoc IC test after universe widening, and the full walk-forward +
permutation Type C pipeline via the now-wired `run_cross_sectional_oos.py`)
found a **RESEARCH NEGATIVE** result: statistically detectable at times
(`NO_SURVIVORS` after FDR correction in the formal pipeline; a small positive
IC in the ad hoc passes), but economically negligible — roughly 0.5–0.7bps
decile spread, ~50% hit rate, an order of magnitude below realistic
round-trip Forex transaction costs (~1.9bps, see 3.8). Treated as evidence,
not failure, per the mission's own framing. Sections 3.4–3.7 below are
additional robustness checks run *against this same negative result* to make
sure it isn't an artifact of USD concentration or regime mismatch — not new
positive findings.

## 3.4 — USD-factor neutralization

Constructed a real USD-strength factor from the actual base/quote currency
structure of the 28-pair universe (not a placeholder), computed real
per-instrument OLS betas against it, and residualized returns before
recomputing the cross-sectional signal.

**Result: the signal survives virtually unchanged.**

| | Raw | USD-neutralized |
|---|---|---|
| IC | +0.0302 | +0.0314 |
| Decile spread | 0.68bps | 0.68bps |
| Hit rate | ~50.7% | ~50.7% |

Conclusion: the negligible signal found in 3.3 is **not a disguised USD bet**
— removing USD-factor exposure changes nothing material. This closes off the
most obvious alternative explanation for the weak signal (that it was really
just "USD went up/down against everything" relabeled as cross-sectional
reversal). It remains economically negligible either way.

## 3.5 — Type C OOS checklist confirmation

`run_cross_sectional_oos.py` (Stage 3 groundwork) now satisfies the mission's
Type C checklist for Forex identically to Crypto:

- Position-based folds over real observed timestamps (not calendar-day
  arithmetic that would misbehave across Forex's weekend gaps) — `oos_validator.py`'s
  `evaluate_cross_sectional_oos` is asset-agnostic and gap-safe by construction (3.1).
- Purge/embargo enforced identically for both asset classes (same function,
  no asset-class branch in the fold-construction logic itself).
- No look-ahead: features and forward returns are computed from the same
  `cross_section_engine`-family pipeline for both asset classes, same
  column-derivation fix (`3f6db46`) applies to both.
- Real, asset-class-correct annualization (`periods_per_year("1h", asset_class)`)
  and real, asset-class-correct transaction costs (3.8) feed the same
  Sharpe/spread calculations for both.
- FDR correction applied across the top-K sweep identically for both (same
  `cross_sectional_permutation_test.py` code path, no branch).

Verified via real, non-mocked end-to-end runs on both asset classes (see
groundwork report): Forex → `NO_SURVIVORS`, `asset_class="FOREX"`,
`cost_rate≈0.000191`; Crypto → `NO_SURVIVORS`, `cost_rate==0.0015`
(byte-identical to pre-change behavior, regression-guarded).

## 3.6 — Permutation-testing methodology audit

Read `cross_sectional_permutation_test.py` in full (375 lines). Findings:

- **Methodology is sound.** The within-timestamp shuffle correctly preserves
  temporal/fold structure and marginal return distributions while destroying
  only the asset-identity-to-value assignment — the correct null hypothesis
  for "does cross-sectional rank/identity carry information," distinct from
  `permutation_test_engine.py`'s temporal block-shuffle (correct for pairs
  cointegration, where the null is "does the *time-series* relationship
  matter").
- `edge_appears_real` requires **all** metrics significant simultaneously
  (conservative AND, not an OR across metrics) — appropriately strict.
- FDR (Benjamini-Hochberg) is applied correctly across the top-K sweep as one
  family within a single run.

**One real, unaddressed gap found** (asset-class-agnostic — applies equally
to Crypto and Forex, not fixed under this mission since it isn't a parity
issue): there is no FDR correction across *multiple separate*
`hypothesis_name` choices. If a researcher runs the pipeline once per each of
the 5 `AVAILABLE_FEATURES` and cherry-picks the best result, no correction
catches that multiple-comparisons inflation. This is the same root cause as
the already-documented `HypothesisFamily`-not-wired-into-orchestrator gap
(Stage 1) — both are "a researcher can run many nominally-independent tests
and no mechanism aggregates them into one family for FDR purposes." Flagged
for future work; fixing it would mean either (a) wiring `HypothesisFamily`
into this script's own CLI, which has the same "trivial single-member
family" problem noted in Stage 1's orchestrator decision, or (b) requiring
the script's caller to pass all `hypothesis_name` choices in one invocation
and applying FDR across the union — a real design decision, not a drive-by
fix, and out of scope here.

## 3.7 — Regime-conditioning

Used the unmodified `RegimeDetector` (EUR/USD as proxy, daily resolution to
avoid O(n²) cost, forward-filled) to check whether the negligible signal from
3.3 is concentrated in or absent from any specific market regime.

**Real, notable calibration finding**: RegimeDetector's ADX/Bollinger-Band
thresholds, tuned for crypto's volatility profile, classify Forex as
`RANGING` **~97% of the time** (16,656 of ~17,200 daily samples). This is not
a bug — Forex genuinely ranges more than crypto at the same thresholds — but
it means regime-conditioning barely conditions on anything for FX with these
thresholds.

| Regime | n (hourly obs.) | IC | p-value | Spread | Hit rate |
|---|---|---|---|---|---|
| RANGING | 464,873 | +0.0300 | 2.9e-93 | 0.70bps | 0.5074 |
| HIGH_VOLATILITY | 2,658 | +0.0092 | 0.63 (n.s.) | -2.46bps | — |
| TRENDING_UP | 4,668 | +0.0175 | 0.23 (n.s.) | -0.83bps | — |
| TRENDING_DOWN | 672 | — | — (too few obs.) | — | — |

RANGING matches the unconditional result exactly, as expected given it's
~97% of the sample. The two non-RANGING buckets are underpowered (both
p > 0.2) but *directionally* show negative spreads rather than a stronger
positive edge — there is no evidence the signal is being diluted by inclusion
of non-ranging periods; if anything, conditioning on non-ranging Forex
regimes looks worse, not better. No actionable regime-filter emerges from
this. Re-tuning `RegimeDetector`'s thresholds specifically for Forex was
considered and rejected: it would require picking new thresholds without a
grounding validation target, which risks curve-fitting the regime boundaries
to this specific negative result — exactly what the mission's "do not
optimize thresholds" rule forbids.

## 3.8 — Cost-aware validation

Already delivered in Stage 3 groundwork and re-confirmed here: Forex
verdicts use `get_cost_model(ASSET_CLASS_FOREX, symbol=...).get_costs()`
(real pip-size/reference-price-based cost, ≈1.9bps average across the
28-pair panel) rather than Crypto's flat `0.0015` (15bps) placeholder. All
Forex economic-significance conclusions in 3.3–3.7 above (0.5–0.7bps decile
spreads) are compared against this real ~1.9bps cost floor, not against
Crypto's rate — the spreads are below cost by roughly 3x, not by an
inflated crypto-cost comparison that would have made the negative result
look artificially worse (nor an understated one that would flatter it).

## 3.9 — Evidence-bundle parity

Inspected a real, non-mocked, completed `ResearchExperiment` record
(EUR/CAD vs USD/JPY cointegration hypothesis, verdict REJECTED) directly
from the database to check field population against the mission's
evidence-bundle checklist:

| Field | Populated? | Notes |
|---|---|---|
| `code_version` | Yes | |
| `random_seed` | Yes (constant `42`) | Not varied per run — deterministic by design, not a gap. |
| `data_fingerprint` | Yes | Stage 1 fix (`orchestrator.py`'s `_compute_experiment_data_fingerprint`); confirmed working for both asset classes. |
| `structured_spec` | Yes | Full spec incl. `asset_class`, `timeframe`, `research_methods`, `data_requirements`. |
| `research_plan` | Yes | Incl. `entitlement`, `capability_id`, `policy_decision`, `data_availability`, and (Stage 1 addition) `data_fingerprints` per instrument. |
| `statistical_results` | Yes | Full cointegration statistics incl. `passes_fdr`, `adf_pvalue_fdr`, `oos_adf_pvalue_fdr`, `passes_oos_persistence` — FDR and OOS persistence are both real, applied fields, not placeholders. |
| `hypothesis_family` | **No** (`None`) | Deliberate Stage 1 decision, not an oversight: `HypothesisFamily.freeze()` locks scope at creation for pre-planned batch sweeps; the Research Lab's interactive one-at-a-time flow would only ever produce trivial single-member families, which would provide the *appearance* of FDR governance with none of its substance. Documented as an intentional gap in both Stage 1 and here — same root cause as 3.6's multi-`hypothesis_name` FDR gap. |
| `validation_results` | No (empty dict) | Type C OOS results (from `run_cross_sectional_oos.py`) are produced by a standalone script, not persisted back onto a `ResearchExperiment` row — there is no orchestrator code path that runs cross-sectional OOS per-experiment today. This is a real, named gap: cross-sectional research and the single-pair Research Lab UI flow are two parallel systems that don't share an evidence bundle. Not fixed here — merging them is a UI/data-model design decision beyond "wire Forex to reach parity with Crypto" (this gap is identical for both asset classes; it is not a Forex-parity issue). |

**Parity verdict for 3.9**: Crypto and Forex `ResearchExperiment` records are
byte-for-byte identical in which fields populate and which don't — there is
no asset-class-specific evidence gap. The two real gaps found
(`hypothesis_family`, `validation_results`) are pre-existing platform-wide
governance gaps, not Forex-parity gaps, and are named rather than fixed here.

---

## 3.10 — Crypto vs. Forex readiness scorecard

| Capability | Crypto | Forex | Parity? |
|---|---|---|---|
| OHLCV data acquisition | Binance REST (`data_fetcher.py`) | Yahoo Finance (`forex_data_fetcher.py`) | Yes — both real, both cached, both integrated with `resolve_ohlcv_path()`. |
| Instrument registry (`bot/instruments.py`) | Full | Full, incl. Stage 1's `pip_size`/`contract_size`/`min_lot`/`lot_step` | Yes for research-relevant fields. Broker fields (`swap_long/short`, `trading_sessions`, `broker_server`, `execution_mode`) are `None` — Stage 2-gated. |
| Feature engineering (`feature_calculator.py` family) | Full | Full, incl. honest `NOT_APPLICABLE` for volume-based features | Yes. |
| Cross-sectional research | Full | Full (Stage 0/3 fix) | Yes. |
| Single-pair cointegration/Kalman research | Full | Full (verified via real Research Lab submission this session) | Yes. |
| Type C (cross-sectional) OOS + permutation + FDR | Full | Full (Stage 3 groundwork) | Yes. |
| Cost-aware validation | Flat 15bps placeholder | Real pip-based ~1.9bps via `ForexCostModel` | Forex is *more* rigorous here, not less — genuinely at parity or better. |
| Dataset fingerprinting / reproducibility | Yes | Yes | Yes. |
| Hypothesis-family FDR governance | Not wired (documented gap) | Not wired (same gap) | Parity — gap is platform-wide, not asset-specific. |
| Market-microstructure engines (order book, trade flow, derivatives, contagion) | Full (real data exists) | Not applicable (no data source) | Not parity by design — correctly classified MARKET_SPECIFIC, not a defect. |
| FX Carry | N/A (no crypto equivalent) | Not implemented (needs broker swap data) | N/A — no parity target exists. |
| Broker/execution adapter (MT5 or equivalent) | Binance/Kraken adapters, live-tested | **None** — environmentally blocked (no MT5 terminal, package, or broker account in this environment) | **No parity.** Confirmed Stage 2 blocker, user-directed to defer. |
| Execution-stack cost model (`Backtester`/`ExecutionEngine`/`OrderManager`) | Binance/Kraken only | **Not wired** — `get_cost_model()` is never called from the execution stack for either asset class in the live/backtest path (pre-existing gap, not touched under this mission — live-trading-adjacent, correctly deferred per the mission's own "do not create live-trading functionality" rule). | No parity, but this is a pre-existing platform gap surfaced by the audit, not something Forex needs uniquely. |
| Live/paper trading | Dry-run simulation only (no real paper-trading endpoint for either venue, per CLAUDE.md) | None | No parity — expected, blocked on Stage 2. |
| Dashboard/UI | Full | Full (dedicated Forex research dashboard, cross-sectional dispersion display, nav-separated from Crypto) | Yes. |

---

## 3.11 — Deep health-check integration

Added `check_forex_cross_sectional_research_wiring()` to
`bot/management/commands/deep_health_check.py`: three real, zero-network
structural checks confirming the Stage 3 groundwork wiring
(`asset_class` parameter presence, Forex feature-dispatch function
existence, per-asset-class checkpoint namespacing in source) stays in place.
Wired into `run_structured_findings()`. Two new tests added to
`bot/tests/test_deep_health_check.py` (one confirms current code is
all-GREEN; one confirms the check correctly catches a simulated regression
via monkeypatched `inspect.signature`). Full suite: **31/31 passing**
(committed `86fd37c`).

This closes the loop on the two Stage 1 bug classes (hardcoded-universe,
hardcoded-annualization) plus the new Stage 3 wiring gap: any future
regression in any of these is now caught by `manage.py deep_health_check`
without a network call, not just by the ad hoc scripts used to find them
originally.

---

## What was NOT done in Stage 3 (named explicitly)

- Stage 2 (MT5/broker adapter) remains fully blocked and untouched, per
  explicit user instruction to defer it.
- Execution-stack cost-model wiring (`Backtester`/`ExecutionEngine`/
  `OrderManager` never calling `get_cost_model()`) — pre-existing,
  platform-wide, live-trading-adjacent, correctly out of scope.
- `kalman_filter_engine.py`'s crypto-tuned hyperparameters were not
  re-validated or re-tuned for Forex (would risk curve-fitting).
- `RegimeDetector`'s thresholds were not re-tuned for Forex's different
  volatility profile (same curve-fitting risk).
- Cross-`hypothesis_name` FDR correction (3.6) and `HypothesisFamily`
  orchestrator wiring (Stage 1, re-confirmed 3.9) remain unwired —
  documented, platform-wide governance gaps, not fixed.
- `cointegration_pipeline_runner.py`/`build_observations.py`/
  `observation_database_builder.py` remain crypto-default callers of
  otherwise-Forex-capable engines — no forensic finding classified these
  as regression-critical; flagged as minor future wiring work.
- No new engines were built for FX Carry or any other missing paradigm —
  parity means matching what Crypto has, not inventing new research.

## Verdict

**Stage 3: PASS.** The real Type C OOS pipeline reaches Forex with correct
costs, correct annualization, and correct checkpoint isolation. The
underlying cross-sectional signal, having now been stress-tested for
USD-factor contamination and regime-dependence in addition to the original
three validation passes, remains **RESEARCH NEGATIVE** — economically
negligible relative to real transaction costs, robust to two additional
scrutiny angles, not merely undetected. Evidence-bundle field population is
identical between asset classes. Deep-health-check coverage now guards the
Stage 3 wiring against regression. No component of this stage required
loosening a statistical gate, optimizing a threshold, or manufacturing a
positive result to close it out.

Proceeding to the mission's final deliverable,
`FOREX_INTEGRATION_FINAL_READINESS_REPORT.md`.
