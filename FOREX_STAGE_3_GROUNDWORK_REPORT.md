# Forex Integration — Stage 3 Groundwork (Partial)

**Scope note, upfront**: this is NOT the full Stage 3 program from the
three-stage mission (`FOREX_STAGE_3_RESEARCH_PARITY_REPORT.md`'s full
scope — the 30-research-paradigm engine-by-engine audit, factor-
neutralization re-investigation, regime-aware research, cost-aware
validation, evidence bundles, readiness scorecard). Stage 2 (MT5/broker
adapter) is **blocked**, not completed — this environment has no MT5
terminal, no `MetaTrader5` package, and no broker account, and the
mission forbids requesting broker credentials of any kind (see the
Stage 2 blocker discussion in this session). Given that, the explicit
direction was to skip Stage 2's broker work and do concrete Stage 3
groundwork instead: the single item Stage 1's own audit named as **the
top Stage 3 priority**. This report covers exactly that one fix, not the
broader program.

## What Stage 1 found

`bot/research/run_cross_sectional_oos.py` — the real Type C OOS
orchestrator and the actual entry point for every cross-sectional
experiment this platform runs — only ever called the crypto-only
`cross_section_engine.run_cross_section_research()`. The Forex-parallel
engine (`run_forex_cross_section_research()`) already existed (built in
the immediately-preceding cross-sectional research audit) but was never
reachable from here. Three additional crypto-shaped assumptions were
hardcoded unconditionally: `cost_rate=0.0015` ("binance round-trip"),
`fingerprint_dataset(source="binance_spot_klines_cross_sectional",
venue="binance", ...)`, and `min_train_periods=8760` ("24×365").

## What was changed

`bot/research/run_cross_sectional_oos.py`'s `run_cross_sectional_research()`
gained an `asset_class: str = ASSET_CLASS_CRYPTO` parameter (default
preserves every existing call site's exact behavior):

- **Feature computation dispatch**: `asset_class == FOREX` now calls
  `run_forex_cross_section_research()` (writing to the already-established
  segregated `research_data/forex/` subdirectory); everything else is
  unchanged.
- **`cost_rate`**: changed from a literal `0.0015` default to
  `Optional[float] = None`. An explicit value always wins (unchanged
  contract). When not supplied: CRYPTO resolves to the exact original
  `0.0015` (verified byte-identical); FOREX resolves a real, evidence-
  based average round-trip cost via `bot.config.cost_model.get_cost_model()`
  across the panel's actual instruments — confirmed empirically at
  ~1.9 bps for the current 28-pair universe, roughly 8× lower than
  crypto's default, which would have overstated Forex transaction costs
  by that same factor in every downstream economic-significance check.
- **Dataset fingerprint provenance**: `source`/`venue` now resolved from
  a small per-asset-class table (`binance_spot_klines_cross_sectional`/
  `binance` for CRYPTO, unchanged; `yahoo_finance_forex_cross_sectional`/
  `yahoo_finance` for FOREX) instead of being hardcoded to Binance for
  every run regardless of what was actually tested.
- **`min_train_periods`**: now `periods_per_year("1h", asset_class)`
  instead of the bare literal `8760`. Identical value for CRYPTO (8760.0,
  zero behavior change); ~6,240 for FOREX's real ~260-trading-day
  calendar — the old crypto number would have required ~1.4 real years
  of Forex history to satisfy a "1 year train floor," needlessly
  shrinking the number of available walk-forward folds.
- **A second real bug found and fixed while implementing the above**:
  `checkpoint_dir` and the default `verdict_output_path` were namespaced
  by `hypothesis_name` (and `rebalance_frequency`, for checkpoints) but
  **not** by `asset_class` — testing the same `hypothesis_name` (e.g.
  `"cross_sectional_zscore"`) for both CRYPTO and FOREX would have
  silently collided: FOREX loading CRYPTO's cached permutation-sweep
  checkpoint, or overwriting CRYPTO's verdict JSON file. Both are now
  namespaced by `asset_class` too; CRYPTO's default verdict path is
  unchanged (`DEFAULT_VERDICT_PATH`, exactly as before) since no existing
  caller ever collided with itself.
- The output dict gained `asset_class` and `cost_rate` fields (matching
  the existing `rebalance_frequency` provenance-transparency pattern) so
  a reader of any verdict JSON can see exactly which asset class and
  cost assumption produced it, not just infer it.

No change was made to `cross_section_engine.py`, `oos_validator.py`, or
`cross_sectional_permutation_test.py` — this was purely a dispatch/
provenance fix in the one orchestrator file, reusing every existing,
already-verified engine unchanged.

## Verification (real, not mocked — matching this project's convention)

Ran the complete, real Type C pipeline end to end for both asset classes
(feature computation → long-format reshape → walk-forward OOS →
permutation sweep → verdict):

- **FOREX** (`cross_sectional_zscore`, all 28 registered pairs,
  n_permutations=2-5): completed successfully. Fingerprint correctly
  tagged `yahoo_finance`. Cost rate resolved to a real ~0.00019 (1.9 bps)
  average across the 28 instruments. Verdict: `NO_SURVIVORS` — consistent
  with the earlier, simpler ad hoc IC-based preliminary test's own
  RESEARCH NEGATIVE finding for the same phenomenon, which is a genuine
  cross-check, not a coincidence: two independently-computed pipelines
  (a quick Spearman-IC script and the full walk-forward-plus-permutation
  evaluator) agree there's no real cross-sectional edge in this universe.
- **CRYPTO** (`cross_sectional_zscore`, ~100 symbols, n_permutations=2-3):
  completed successfully. `cost_rate` confirmed exactly `0.0015`,
  `asset_class` correctly `"CRYPTO"` — byte-identical to pre-change
  behavior. Verdict: `NO_SURVIVORS` (this run was for regression
  verification, not a new research finding — the crypto cross-sectional
  question was already answered honestly in earlier work this session).

**Tests**: `bot/tests/test_run_cross_sectional_oos_forex.py` (new, 5
tests) — invalid `asset_class` fails closed before any expensive
computation; a real Forex run gets real Forex provenance/cost; a real
Crypto run's `cost_rate` is asserted exactly `0.0015` (the regression
guard that matters most); `checkpoint_dir`/default `verdict_output_path`
are confirmed (via source inspection, not a second expensive live run)
to include `asset_class` in their computed paths. **5/5 pass.** These are
intentionally slow tests (~15 minutes total) because they exercise a
real walk-forward-plus-permutation research pipeline end to end, not a
design flaw — matching this project's own "no mocking, real endpoints"
convention already applied throughout its test suite.

## What this does NOT cover (explicitly, not silently)

This is groundwork, not Stage 3 completion. Still open, and still
correctly scoped to a full Stage 3 pass if/when undertaken: the 30-
research-paradigm engine-by-engine capability audit (3.1/3.2);
`run_research_all.py`'s own separate crypto-only `SYMBOLS` hardcoding
(the *other* entry point Stage 1 flagged, not touched here);
`kalman_filter_engine.py`'s untuned-for-Forex hyperparameters; a formal
factor-neutralization re-investigation of the cross-sectional signal
(mission 3.4); Type C OOS's purge/embargo semantics were reused exactly
as already built and verified for Crypto, not independently re-audited
for Forex-specific overlap concerns beyond what Stage 1's audit already
covered; regime-conditioned Forex research; a full cost-aware validation
pass beyond the one `cost_rate` default fixed here; a Research Lab
evidence bundle; the Crypto-vs-Forex readiness scorecard; deep-health-
check integration for this specific pipeline. None of these were
attempted, and none should be assumed done.

## Verdict

The one, concrete, named Stage 3 priority from Stage 1's audit is fixed,
tested, and verified with real end-to-end runs on both asset classes,
with zero Crypto regression. This is a real, load-bearing step — the
platform's actual Type C OOS research pipeline can now honestly test
Forex hypotheses with correct provenance and correct costs, not just in
principle via a standalone engine — but it is one fix, not a completed
stage. Stage 2 remains blocked (not failed — blocked by a real
environmental constraint, explicitly not bypassed). The full Stage 3
program has not been started.
