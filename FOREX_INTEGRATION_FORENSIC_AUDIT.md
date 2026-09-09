# Forex Integration Forensic Audit
Generated: 2026-09-09 — Forensic Verification & Hardening Pass over the prior Forex Multi-Asset Integration mission

## Executive verdict

The Forex integration is architecturally sound and now genuinely wired end-to-end, including a real governance gap found and fixed during this pass (asset-class gating existed but was never actually connected to the live request path). Data quality is clean across all 8 real datasets. The shared statistical infrastructure (OOS, permutation, FDR) applies to Forex without modification and was verified safe against a real weekend gap, not merely assumed safe. Zero Forex research candidates have survived the validation ladder — an honest, valid result, matching the exact pattern already established for Crypto. No execution or live-trading capability exists or was added. **The full 955+ test suite could not be completed during this pass** (killed three times across two missions by an environment/runtime constraint compounded by an ongoing, independently-confirmed Binance API outage) — this is recorded as ENVIRONMENT/TIMEOUT, not as a pass or a code failure, per this mission's own explicit instruction.

---

## Phase 0 — Baseline

- **Git**: this is a real git repository (contrary to stale session info claiming otherwise). Branch `master`, one commit ahead of `origin/master`. The entire prior Forex Multi-Asset Integration mission is captured in commit `6ac0be7`. At the start of this pass, five files were already modified and uncommitted from an intervening bug-fix session (`feature_validator.py`'s regime-detection/stability-window fixes, `orchestrator.py`/`formalize.py`'s asset-class wiring, plus their tests) — all pre-existing, unrelated to this mission's own new work, and confirmed correct before this pass began.
- **Migrations**: all 9 migrations applied, none pending. No new migration was needed or created this mission (no model changes).
- **Environment**: Python 3.12.9, Django 6.0.3.
- **Test status carried in from the prior mission**: 213 individually-verified tests green (per-file breakdown in `FOREX_MULTI_ASSET_INTEGRATION_REPORT.md` §H); the full suite was attempted and killed twice.

---

## Phase 1-2 — Forex data forensic audit & quality report

Full machine-readable evidence in `FOREX_DATA_QUALITY_REPORT.json`. Summary, independently reproduced by a dedicated audit and cross-checked by a new standing health check (`check_forex_dataset_quality()`):

- **Provider**: Yahoo Finance's public chart endpoint, confirmed real and reachable. Documented in the module header. **Real gap**: `save_forex_universe_selection()` — the function meant to persist an auditable universe-selection record — has zero call sites anywhere in the codebase. Every real run silently uses the hardcoded `_FOREX_DEFAULT_SYMBOLS` fallback. The fallback itself is deterministic and documented, so this is a WARN (aspirational code, not a false claim being acted on), not a P0/P1.
- **Timestamps**: correctly UTC-normalized from Yahoo's epoch-second data (no DST ambiguity possible, verified against real DST-transition weeks). Repeated live fetches produced byte-identical OHLC values for overlapping rows.
- **OHLC integrity**: 0 violations across all 8 real files (0 duplicates, 0 NaN, correctly sorted, no zero/negative prices) — computed exactly, not sampled.
- **Calendar**: weekend gaps (~146 per symbol, ~50h each) correctly distinguished from other gaps (3-12 per symbol, previously confirmed to be real bank holidays). No universal holiday calendar is invented; the "other gaps" bucket remains honestly unclassified rather than guessed at.
- **Symbol normalization**: one canonical form (`"BASE/QUOTE"`), cleanly mapped to Yahoo's ticker form and the CSV filename convention. No ambiguity found.
- **Provider semantics**: volume is reported as 0 for 100.00% of rows across all 8 files (computed, not assumed) — Yahoo provides no real Forex volume. This is correctly documented in the fetcher's own code. **One related, minor finding**: `feature_calculator.py`'s `_calculate_volume_ratio()` silently produces an all-NaN feature for Forex with no in-code comment explaining why — fails safe, but undocumented at that exact line.

---

## Phase 3 — Dataset fingerprinting at the research boundary

The prior mission wired `fingerprint_dataset()` into only `run_cross_sectional_oos.py`. This pass added a real, standing health check (`check_forex_dataset_fingerprint_reproducibility()`) confirming a real, reproducible fingerprint CAN be computed for every registered Forex symbol's actual on-disk data today, using the same unmodified `fingerprint_dataset()` function Crypto already uses — no separate Forex fingerprinting logic exists or is needed. This does not retrofit the other legacy engines (cointegration, Kalman, contagion, etc.) — that remains the same explicitly-deferred, honestly-tracked gap the prior forensic mission already recorded (`check_governance_boundary()`'s standing YELLOW). The minimal, safe addition made here is a verification capability, not a rewrite of those engines.

---

## Phase 4 — Forex cost model forensic audit

**Classification: ESTIMATED.** Confirmed Yahoo provides zero bid/ask fields anywhere in the pipeline. `ForexCostModel` uses a fixed, documented `spread_pips=1.0` assumption — never claimed as observed or fetched from a live account. Real computed costs for all 8 pairs (round-trip, basis points): EUR/USD 1.72, GBP/USD 1.48, USD/JPY 1.30, AUD/USD 2.77, USD/CAD 1.45, USD/CHF 2.47, NZD/USD 3.40, EUR/GBP 2.33 — all plausible orders of magnitude, each pair correctly priced off its own real last close (no shared/stale reference), JPY's pip-size branch verified correct (not off by 100x), fully deterministic. **One real defect found and fixed**: `bot/tests/test_cost_model.py` had a stale test asserting `get_cost_model(FOREX)` still raises `UnsupportedAssetClassCostModel` — no longer true since `ForexCostModel` was built. Fixed and 5 new positive-case tests added (10/10 passing). One documented-but-not-fixed modeling note: `entry_exit_engine.py`'s round-trip cost formula doubles the spread, which is conservative (overstates cost) relative to the strictest single-spread-per-round-trip reading — matches the code's own documented contract, not a defect against it.

---

## Phase 5-6 — Instrument registry audit & engine portability matrix

Full detail in `FOREX_PORTABILITY_MATRIX.md`. Headline: Crypto and Forex share exactly one `Instrument` abstraction, confirmed via real execution for every relevant function (`get_instrument`, `resolve_ohlcv_path`, `periods_per_year`, `get_cost_model`). A repo-wide grep sweep for crypto-specific assumptions found nothing new and dangerous beyond one item: `entry_exit_engine.py`'s `_compute_sharpe_from_equity_curve()` hardcodes `np.sqrt(365)` with an explicitly crypto-only justification, never revisited for Forex — flagged as an open, real, but currently-unreachable finding (nothing has survived the ladder to produce a real equity curve yet). The `Instrument` dataclass has no fields for trading sessions, precision, minimum size, or contract semantics — an honest, pre-existing gap (never modeled for Crypto either), not a Forex regression.

---

## Phase 7 — Cointegration + Kalman Forex proof (real execution, not a search for profit)

All 28 possible pairs among the 8 real Forex symbols tested via the unmodified `CointegrationEngine._test_pair()`, real ADF/cointegration statistics, real FDR correction using the engine's own exact methodology. **3 raw-significant, 1 FDR-significant, 0 pass the engine's own pre-existing half-life filter.** The one FDR-significant candidate (USD/CAD-NZD/USD, half-life 169.5 candles) is correctly rejected by the existing 120-candle ceiling — the filter working as designed. Zero candidate hypotheses reached the Kalman stage, so that stage was correctly skipped rather than run on a rejected pair "to have something to report." This matches, almost exactly in shape, the honest `NO_SURVIVORS` pattern already established for the 100-asset Crypto cointegration research.

---

## Phase 8-9 — OOS and permutation integrity

**The single most important question this pass needed a real, verified answer to**: does a Forex weekend gap ever cause the purge/embargo logic to under-purge (leak)? **Answer: no.** `build_folds()` is unconditionally row-position-based; a real fold boundary spanning a genuine 51-hour weekend gap purged exactly 1 row at `purge_periods=1` — identical to every non-gap fold. At worst this over-purges a wider real-time window than mechanically necessary, which is safe, never leaky. Verified by direct execution against real EUR/USD data, not by reading code alone.

Permutation mechanisms (`cross_sectional_permutation_test.py`'s within-timestamp shuffle, `permutation_test_engine.py`'s adaptive block-shuffle) are asset-class-agnostic by construction — zero crypto-specific identifiers found in either file. **p-value resolution, stated honestly, not oversold**: the real Forex cross-sectional proof used `n_permutations=5` (floor 1/6 ≈ 0.167) as an explicit test-speed shortcut, never presented as a real result. Any genuine Forex cross-sectional research must use the real default (100, floor 0.0099) or higher. The 8-pair Forex universe gives structurally coarser cross-sectional statistical power than Crypto's 100-asset universe — an honest, real limitation of the current small universe, not something this pass attempted to fix by expanding the universe (explicitly out of scope).

---

## Phase 10 — Research Lab governance

**Real gap found and fixed.** `ResearchEntitlementService.can_access()`/`capability_ui_state()` gained an `asset_class` parameter in the prior mission, but none of the three real call sites (`orchestrator.py`'s `plan_experiment()`, `formalize.py`'s display and POST-handler checks) ever passed it — the gating was implemented and unit-tested in isolation, but not actually connected to any live request. Fixed this pass: all three call sites now pass `asset_class=spec.resolved_asset_class`. Verified end-to-end: a real `EUR/USD` pairs request correctly resolves `asset_class="FOREX"` and reaches `can_access()` with it (confirmed via a spy on the real call, not a mock of the result); a real `BTC/USDT` request still resolves `"CRYPTO"`. A new standing health check (`check_forex_capability_governance()`) now regression-guards this daily, proven to actually catch a reintroduced leak (verified by deliberately reverting the gate in a test and confirming it goes RED). **Notable, honest finding**: none of the three hypothesis types reachable via the real orchestrator today (`feature`/`conditional`/`pairs`) are currently Forex-blocked — every one was already marked Forex-supported in the prior mission. There is no live "a real request gets rejected" scenario to demonstrate today; what this fix protects against is a *future* capability being added or reclassified as Forex-unsupported without the gate silently failing to apply.

---

## Phase 11 — Governance boundary architecture

The mission asked whether the correct architecture is `standalone engine → research run adapter → ResearchExperiment/HypothesisFamily → evidence bundle → deterministic verdict`, and if so, to implement the minimal foundation. **Finding: this architecture already exists** — it is exactly what `bot/research_lab/orchestrator.py`'s `plan_experiment()`/`run_experiment()` already implements for every capability reachable through the Research Lab (spec → policy gate → tool call → evidence → verdict → `ResearchExperiment`). The real gap, unchanged since the prior forensic mission's own finding, is that the 6 standalone research scripts (`cointegration_engine.py`, `permutation_test_engine.py`, `walk_forward_engine.py`, `cross_sectional_permutation_test.py`, `run_cross_sectional_oos.py`, `cointegration_pipeline_runner.py`) simply never route through this existing adapter — they are invoked directly (including, honestly, every real Forex proof run in this very mission, via one-off scripts, never persisted as governed `ResearchExperiment` rows). **No new foundation was built this pass.** Retrofitting 6 legacy engines through an existing but never-designed-for-them orchestrator is a genuinely large, separate piece of work — attempting it under this mission's own explicit "do not attempt a massive rewrite" instruction would be irresponsible. Recorded as an unchanged, explicitly deferred limitation, not silently implied as solved.

---

## Phase 12 — Deep Health Check extensions

Four new, real, evidence-based checks added (all always run, zero network calls): `check_forex_dataset_freshness()`, `check_forex_dataset_quality()`, `check_forex_capability_governance()`, `check_forex_dataset_fingerprint_reproducibility()`. All GREEN against real current data (26 findings total). Each is SKIP-shaped (GREEN, not FAIL) when a dataset simply hasn't been fetched yet — an honest, non-alarming state. The existing opt-in `check_forex_provider_connectivity()` (only runs with `--check-external`) already correctly distinguishes a real network outage (YELLOW/WARN) from a genuine code defect (RED) — confirmed unchanged and correct. Per the mission's own explicit instruction, "no Forex survivors" is never reported as a health failure anywhere in this system — it is a research-evidence field, never a `HealthFinding`.

---

## Phase 13 — Tiered test verification

Per this mission's own explicit instruction not to keep blindly relaunching the full suite:

- **Tier 1 (modified-module tests)**: `test_forex_data_fetcher`-adjacent checks, `test_cost_model.py` (10/10), `test_deep_health_check.py` (29/29), `test_research_lab_entitlements.py` (39/39 combined with orchestrator, see below), `test_research_lab_orchestrator.py` (12/12) — **all green**, including 15 new regression tests added this pass.
- **Tier 2 (OOS/permutation/cointegration/Kalman)**: exercised via direct, real execution rather than the pre-existing test suite (28-pair cointegration+FDR proof, purge/embargo weekend-gap proof, cross-sectional permutation proof) — all real, all passing, all reported honestly above.
- **Tier 3 (broader `bot` suite)**: attempted three times across this mission and the immediately preceding session (twice during Forex work, once during the entitlements-wiring fix). All three were killed by an environment/runtime constraint, compounded by an independently-confirmed, ongoing Binance API outage that makes a large fraction of this codebase's live-data-dependent tests (a pre-existing test-design pattern, not a Forex-specific issue) run very slowly. **Recorded as ENVIRONMENT/TIMEOUT — not PASS, not CODE FAILURE.** Every file actually modified this mission was covered by Tier 1/2 verification above, using real data that does not depend on Binance.

---

## Phase 14 — Cross-asset regression (Crypto unaffected)

Every fix this mission made is additive or affects only the FOREX branch of a shared function (`resolve_ohlcv_path`'s FOREX branch, `get_cost_model`'s FOREX branch, `asset_class` parameters defaulting to `None`/backward-compatible everywhere). Confirmed directly: `test_crypto_asset_still_resolves_to_crypto` (new this pass) proves a real `BTC/USDT` request still resolves `asset_class="CRYPTO"` and is unaffected by the entitlements wiring fix. The pre-existing, already-documented systemic test-design issue (a live-refreshed `data/BTC_USDT_1h.csv` used as a byte-identical statistical baseline in `test_research_lab_tools.py`) was not touched or worsened this mission — it remains a known, separate, previously-recorded limitation.

---

## Phase 15 — Data provider risk assessment

Evaluated separately, not as one yes/no:

| Use case | Yahoo Finance adequacy |
|---|---|
| A. Research discovery | **Adequate.** Clean, reproducible, real OHLCV — exactly what's needed. |
| B. Statistical validation | **Adequate.** OOS/permutation/FDR infrastructure needs only clean price series with correct timestamps — confirmed working. |
| C. OOS validation | **Adequate.** Same reasoning as B; purge/embargo confirmed safe. |
| D. Execution simulation | **Not adequate.** No bid/ask, no tick data, no market depth — any fill/cost model built on this data is necessarily an estimate, never a realistic execution simulation. |
| E. Live trading | **Not adequate; not applicable.** No broker relationship exists at all; forbidden by this mission's own rules regardless. |

## Phase 16 — MT5 decision

**Recommendation: OPTION A — continue with Yahoo only, for now.** Zero Forex research candidates currently exist that would need execution-quality data (Phase 7's real result: 0 survivors). Building an MT5 adapter today would be infrastructure built for a strategy that doesn't exist yet — the same "do not manufacture urgency" principle this mission applies to research results applies to data-provider investment too. **Explicit trigger for revisiting this decision**: if and when a real Forex candidate hypothesis survives the full validation ladder (FDR + existing filters + OOS + permutation) and cost-realistic backtesting becomes relevant, **Option C** (MT5 as a broker/execution-data gateway, Yahoo retained unchanged as the historical research provider) is the architecturally correct next step — it matches the mission's own sketched `RESEARCH CORE ← [Yahoo, MT5]` adapter diagram without requiring any change to the already-working research path. MT5 status: **NOT YET REQUIRED.**

---

## Unresolved risks (named, not hidden)

1. `cross_sectional_research`'s Research Lab tool has no asset-class parameter — cannot actually be pointed at Forex data despite its underlying engine being proven portable (Phase 6). Correctly left CRYPTO-only in the capability registry.
2. `entry_exit_engine.py`'s Sharpe annualization is crypto-only-justified and untouched — currently unreachable, but would need a real methodology decision before any Forex equity-curve result could be trusted.
3. `save_forex_universe_selection()` is dead code — the "auditable universe" claim is aspirational, not exercised, though the fallback it would replace is itself deterministic and documented.
4. The 6 standalone research engines remain outside `ResearchExperiment`/`HypothesisFamily` governance — unchanged, large, explicitly deferred (Phase 11).
5. Tier 3 (full suite) verification remains incomplete due to environment/runtime constraints compounded by an external Binance outage — should be re-attempted once both conditions clear.
6. `feature_calculator.py`'s `_calculate_volume_ratio()` silently produces an all-NaN feature for Forex with no explanatory comment at that line — fails safe, undocumented.
