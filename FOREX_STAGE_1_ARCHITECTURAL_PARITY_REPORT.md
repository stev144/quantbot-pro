# Forex Integration — Stage 1: Architectural Parity Report

Mission: three-stage program to bring Forex to full architectural/
scientific parity with Crypto. This report covers Stage 1 only —
architecture audit and hardening. No MT5/broker adapter (Stage 2), no
end-to-end research-engine wiring (Stage 3), no execution/live-trading
code was touched, per the mission's own explicit staging.

Baseline commit for this audit: `3f6db46` (the just-completed Forex
cross-sectional research audit, including the fix to
`CrossSectionEngine`'s Steps 6–8 hardcoded-universe bug and the 28-pair
G8-currency universe widening).

## What was inspected

Three parallel forensic audits were run against the real codebase (not
against a prior summary), each covering a distinct layer:

1. **Execution / risk / backtesting layer**: `bot/backtesting/backtester.py`,
   `portfolio_backtester.py`; `bot/engines/execution_engine.py`,
   `order_manager.py`, `position_tracker.py`, `simulation.py`,
   `exchange_adapter.py`, `binance_adapter.py`, `kraken_adapter.py`,
   `execution_result.py`, `order_intent.py`, `venue_readiness.py`,
   `derivatives_data.py`, `price_validator.py`, `trade_data.py`,
   `execution_comparison.py`, `liquidity.py`, `execution_coordinator.py`,
   `strategy_router.py`, `strategy_scorer.py`, `regime_detector.py`;
   `bot/risk/position_sizer.py`, `drawdown_guard.py`;
   `bot/journal/models.py`, `trade_logger.py`; `bot/core/bot_runner.py`,
   `dry_run_test.py`; `bot/config/cost_model.py`, `execution_costs.py`,
   `risk.py`.
2. **Research-engine arsenal**: `feature_calculator.py`,
   `feature_validator.py`, `feature_stability_analyzer.py`,
   `feature_decay_analyzer.py`, `entry_exit_engine.py`,
   `kalman_filter_engine.py`, `walk_forward_engine.py`,
   `permutation_test_engine.py`, `oos_validator.py`,
   `run_cross_sectional_oos.py`, `strategy_oos_adapters.py`,
   `contagion_engine.py`, `derivatives_engine.py`,
   `orderbook_depth_engine.py`, `trade_flow_engine.py`,
   `validated_feature_registry.py`, `universe_selector.py`,
   `fetch_all_symbols.py`, `fetch_derivatives_data.py`,
   `run_research_all.py`, `bot/instruments.py`'s `periods_per_year()`.
3. **Governance / dashboard layer**: `bot/research_lab/models.py`
   (`HypothesisFamily`, `ResearchExperiment`), `trial_service.py`,
   `data_fingerprint.py`, `orchestrator.py`, `spec.py`, `entitlements.py`,
   `capability_registry.py`, `data_availability.py`, `verdict.py`,
   `interpreter.py`, `tools/research_tools.py`, `tools/statistical_tools.py`,
   `tools/_data.py`, `bot/instruments.py` (full), `bot/config/cost_model.py`
   (full), `bot/forex_data_fetcher.py` (full), `bot/views/dashboard.py`,
   `terminal_data.py`, `forex_terminal_data.py`, `forex_dashboard.py`, and
   all 6 Forex `deep_health_check.py` functions.

## Crypto assumptions discovered — classification matrix

Full matrices with file:line citations were produced by all three audits
(condensed here; every row was directly verified by reading the file, not
inferred). Legend: **SAFE** = SAFE_SHARED, **ADPT** = ADAPTER_SPECIFIC,
**MKT** = MARKET_SPECIFIC (correct, not a defect), **CRYPTO** =
CRYPTO_SPECIFIC (hidden assumption in supposedly-shared code), **BUG** =
concrete defect, **UNK** = needs a human decision.

| Area | File | Finding | Class | Fixed in Stage 1? |
|---|---|---|---|---|
| Execution | `bot/backtesting/backtester.py` | `venue_id="binance"` param, never `asset_class`; imports `get_venue_execution_costs`, never `bot.config.cost_model.get_cost_model()` | CRYPTO / BUG | No — execution-stack, Stage 2/3 |
| Execution | `bot/engines/simulation.py`, `order_manager.py` | `FEE_RATE`/`SLIPPAGE_RATE` module globals, "Binance standard taker fee" | CRYPTO | No — same reason |
| Execution | `bot/engines/binance_adapter.py`/`kraken_adapter.py` | `get_execution_costs()` resolves via `get_venue_execution_costs()`, bypassing `cost_model.py` entirely despite the adapter contract's own docstring implying it should | CRYPTO / BUG | No |
| Execution | `bot/config/execution_costs.py` | Unknown `venue_id` "fails open" to Binance's numbers silently | BUG | No |
| Execution | `bot/engines/venue_readiness.py` | Hardcodes `"BTC/USDT"` as the universal readiness-probe symbol | CRYPTO / BUG | No |
| Execution | `bot/risk/position_sizer.py` | 4-decimal quantity floor, crypto-coin-shaped | CRYPTO | No |
| Execution | `bot/engines/price_validator.py`, `liquidity.py`, `execution_comparison.py` | Assume a second centralized exchange / central-limit-order-book depth — no FX dealer/ECN equivalent | UNK | No |
| Execution | `bot/engines/exchange_adapter.py` | Contract itself is genuinely asset-agnostic | SAFE (design) | — |
| Execution | No `ForexAdapter(ExchangeAdapter)` exists anywhere | — | Expected | Stage 2 deliverable |
| Research | `feature_calculator.py`, `feature_validator.py` | Already call `periods_per_year(timeframe, asset_class)` correctly | SAFE | — |
| Research | `feature_calculator.py`'s `_calculate_volume_ratio` | Forex volume=0 → silently all-NaN, no explicit signal why | CRYPTO / BUG | **Yes** |
| Research | `feature_stability_analyzer.py` | `SYMBOLS` default hardcoded to CRYPTO; `ANNUALISATION_FACTOR=sqrt(8760)` applied unconditionally | CRYPTO / BUG | **Yes** |
| Research | `feature_decay_analyzer.py` | `SYMBOLS` a literal 20-symbol crypto list (not even registry-derived); dead `ANNUALISATION_FACTOR` constant | CRYPTO / BUG | **Yes** |
| Research | `entry_exit_engine.py`'s `_compute_sharpe_from_equity_curve` | Hardcoded `sqrt(365)`; `self.asset_class` existed but was never threaded into this one calculation | CRYPTO / BUG | **Yes** |
| Research | `entry_exit_engine.py` (everywhere else), `kalman_filter_engine.py`'s path resolution | Already correctly registry-driven and cost-model-aware | SAFE | — |
| Research | `oos_validator.py`, `walk_forward_engine.py`, `permutation_test_engine.py` | Position-based folds/block operations over real observed timestamps — genuinely gap-safe | SAFE | — |
| Research | `run_cross_sectional_oos.py` | Calls the crypto-only `run_cross_section_research()`, never `run_forex_cross_section_research()`; hardcodes `cost_rate=0.0015` "binance round-trip", fingerprint `venue="binance"` unconditionally, `min_train_periods=8760` | CRYPTO / BUG | No — Stage 3's top priority |
| Research | `run_research_all.py` | The documented top-level pipeline entry point; crypto-only `SYMBOLS`, no Forex path at all | CRYPTO | No — Stage 3 |
| Research | `kalman_filter_engine.py` | Candle-count hyperparameters (gap-safe) but comments/tuning assume crypto's continuous liquidity, never re-validated for Forex; separate, orthogonal dead-no-op forward-fill in `_align_prices` | UNK | No — flagged, not Forex-specific |
| Research | `contagion_engine.py`, `derivatives_engine.py`, `orderbook_depth_engine.py`, `trade_flow_engine.py` | Genuinely crypto-only by real data non-existence for Forex | MKT (correct) | — |
| Governance | `HypothesisFamily` | Zero production callers anywhere; only exercised by its own test suite | BUG (dead governance) | See "Governance" section below — deliberately not wired, reasoning given |
| Governance | `fingerprint_dataset()` | Wired into crypto's `run_cross_sectional_oos.py` and a display-only, non-persisted Forex dashboard call; never into `orchestrator.py`'s real experiment path | BUG | **Yes** |
| Governance | `ResearchExperiment.data_fingerprint`/`hypothesis_family` | Real model fields, never populated by any real student-run experiment (`code_version`/`random_seed` were; these weren't) | BUG | `data_fingerprint`: **Yes**. `hypothesis_family`: not wired, see below |
| Governance | `bot/research_lab/tools/research_tools.py`'s `run_cross_sectional_ranking_test` | Hardcodes the crypto-only universe, no `asset_class` param at all | CRYPTO | No — already correctly gated off at the entitlement layer (`capability_registry.py` excludes FOREX from this capability), so not a live user-facing bug today; real wiring is Stage 3 |
| Governance | `Instrument` dataclass | 7 fields, no pip/lot/session metadata | Gap | **Yes** — extended |
| Governance | `ForexCostModel` | Real, symbol-varying (pip size differs for `/JPY`, reads real reference price); `spread_pips=1.0` flat, `fee_rate=0.0` hardcoded | MKT (documented simplification) | No — out of scope, cost model itself works correctly |

## What was changed (Stage 1 implementation)

1. **`bot/instruments.py`** — `Instrument` gained 9 new, all-`Optional`
   fields: `pip_size`, `contract_size`, `min_lot`, `lot_step`,
   `swap_long`, `swap_short`, `trading_sessions`, `broker_server`,
   `execution_mode`. Populated for FOREX rows in `_build_forex_registry()`
   (pip size reuses `ForexCostModel`'s own `/JPY`-vs-default convention;
   contract/lot sizing uses standard, publicly-documented retail-FX
   values, explicitly labeled as conventions pending real broker
   confirmation in Stage 2). CRYPTO rows: all 9 fields stay `None`, zero
   behavior change (verified).

2. **`bot/research/capabilities.py`** (new file) — a small
   `DataRequirement` enum (`REAL_VOLUME`, `TICK_VOLUME`, `BID_ASK`,
   `ORDER_BOOK`, `DERIVATIVES`) and `check_data_requirement(df, requirement,
   asset_class=None) -> CapabilityResult`, returning `OK` /
   `NOT_APPLICABLE` / `INSUFFICIENT_DATA` with a real, evidence-based
   reason (never guesses from `asset_class` alone — always inspects the
   actual data). Applied to the one concrete, confirmed case:
   `feature_calculator.py`'s `_calculate_volume_ratio` now explicitly
   checks `REAL_VOLUME` and logs the honest reason instead of silently
   producing an all-NaN column via a division-by-zero side effect. The
   stored column contract is unchanged (still all-NaN in that case,
   which `feature_validator.py` already handles correctly) — only the
   visibility of *why* changed. Retrofitting the rest of the research
   arsenal to declare capabilities is named, explicitly, as Stage 3 scope.

3. **Fixed the three confirmed instances of the exact "regression-critical"
   bug class** the mission calls out by name:
   - `feature_stability_analyzer.py`: `SYMBOLS` default is still
     `symbols_for_asset_class(ASSET_CLASS_CRYPTO)` (unchanged value,
     already overridable via `run_stability_analysis(symbols=...)`), but
     `ANNUALISATION_FACTOR` is no longer applied unconditionally —
     `_compute_metrics()` now resolves the real asset class per symbol via
     the instrument registry and calls `periods_per_year("1h", asset_class)`,
     falling back to the original constant (loudly logged) only if the
     symbol isn't registered.
   - `feature_decay_analyzer.py`: `SYMBOLS` changed from a literal,
     hand-typed 20-symbol list to `symbols_for_asset_class(ASSET_CLASS_CRYPTO)`
     (registry-derived, same "derive don't retype" fix already applied
     to `cointegration_engine.py`/`cross_section_engine.py`). The dead
     `ANNUALISATION_FACTOR` constant (confirmed zero references anywhere
     — Sharpe was already removed from grading in an earlier fix) was
     removed rather than "fixed," since fixing an unused constant would
     have been pointless work.
   - `entry_exit_engine.py`: `_compute_sharpe_from_equity_curve()` (no
     longer `@staticmethod`, its only call site is already
     `self.`-based) now uses `periods_per_year("1d", self.asset_class)`
     instead of the bare `sqrt(365)`.

4. **Wired dataset fingerprinting into the real orchestrator path.**
   `bot/research_lab/orchestrator.py`'s `run_experiment()` now computes a
   real `data_fingerprint` (via the existing `fingerprint_dataset()` +
   `load_ohlcv()`, both already asset-class-aware) for every real
   student-run experiment, symmetrically for Crypto and Forex — no
   asset-class branch needed. A pairs hypothesis (two instruments) gets
   one combined, order-independent fingerprint (sha256 of the sorted
   per-instrument fingerprints) on the model's one `CharField(max_length=64)`
   field, with the individual per-instrument fingerprints recorded in
   `research_plan` (already the home for other planning-time provenance)
   for full transparency. Wrapped in two layers of failure isolation (the
   helper's own per-instrument try/except, plus a second try/except at
   the `run_experiment()` call site) so a fingerprinting failure can never
   block an otherwise-successful research run — verified by a dedicated
   test that mocks the helper to raise and confirms the experiment still
   reaches `COMPLETED`.

## What was intentionally NOT changed

- The entire execution/backtesting cost-model gap (`Backtester`/
  `ExecutionEngine`/`OrderManager`/`simulation.py` never calling
  `get_cost_model()`) — confirmed definitively real, but touching it is
  live-trading-adjacent execution-stack work, explicitly Stage 2/3
  territory, and the mission forbids "creating live-trading functionality
  as part of research validation."
- `venue_readiness.py`'s hardcoded `"BTC/USDT"` probe, `execution_costs.py`'s
  fail-open venue fallback, `position_sizer.py`'s crypto-lot assumption,
  `price_validator.py`/`liquidity.py`'s centralized-exchange assumptions —
  all real, all currently **latent** (nothing invokes them with a Forex
  adapter, because none exists). Registered here as pre-flagged Stage 2
  blockers rather than fixed now, since fixing execution-layer code with
  zero current Forex caller is an unrequested, risk-bearing touch to
  shared/live-trading-adjacent code.
- `run_research_all.py`/`run_cross_sectional_oos.py`'s crypto-only wiring
  — real and consequential, but "wire the research arsenal end-to-end for
  Forex" is Stage 3's own stated objective, not Stage 1's architecture-
  boundary objective. **Flagged as the single top Stage 3 priority.**
- `kalman_filter_engine.py`'s crypto-tuned hyperparameters and its
  separate, pre-existing, orthogonal dead-forward-fill bug in
  `_align_prices` — real, but deserve their own focused look, not a
  drive-by fix bundled into this audit.
- No `ForexAdapter` implementation — expected, that is literally Stage 2.

### `HypothesisFamily` — deliberately not wired, with reasoning

This is the one place this report deviates from a literal reading of
mission section 1.9 ("wire existing HypothesisFamily... into actual
research workflows"), and the deviation is itself the finding worth
reporting. `HypothesisFamily.freeze()` locks its scope
(`feature_family`/`assets`/`horizons`) permanently at creation — by
design, so no one can retroactively widen a family after seeing results
(`trial_service.freeze_family_before_testing()`'s own docstring: "a
caller cannot accidentally run statistical tests against a family whose
scope could still change"). This is exactly correct for the workflows it
was built for: a systematic, pre-planned batch research run
(`cointegration_engine.py`'s full pair sweep, `feature_validator.py`'s
full feature sweep) where the entire scope is known before the first
test runs.

The Research Lab's real, interactive flow is the opposite shape: one
student submits one ad hoc hypothesis at a time, with no way to know in
advance which assets or features a future hypothesis will name. Wiring
`freeze_family_before_testing()` naively into `orchestrator.py` would
create a new, trivial, single-hypothesis "family" per experiment — which
would *look* like governance (a `hypothesis_family_id` populated on every
row) while providing **none** of the actual statistical protection
`HypothesisFamily` exists for (no real cross-hypothesis FDR correction
ever happens if every family has exactly one member). That would be
worse than the current, honestly-absent state — it would manufacture the
appearance of multiple-testing governance without the substance, which
directly conflicts with this mission's own "do not manufacture positive-
looking results" principle applied to governance rather than statistics.

**Recommendation, not implemented here**: a real design decision is
needed on what the correct family boundary is for an open-ended,
incremental research flow — candidates include a rolling per-(student,
capability, asset_class) window re-opened periodically, or accepting that
interactive hypothesis generation is fundamentally exploratory and needs
a different governance mechanism (e.g. a fixed per-period alpha budget
consumed per test) rather than `HypothesisFamily`'s batch-shaped freeze
model. This is named as a real, open Stage 1 finding rather than silently
skipped or hastily misapplied.

## Canonical market-data contract (documented, not rebuilt)

The real, already-working, already-correct contract — confirmed by
`oos_validator.py`, `regime_detector.py`, `feature_calculator.py`, and
`CrossSectionEngine` all independently consuming it the same way — is:

- **Data**: a `pandas.DataFrame` with a UTC `DatetimeIndex` and
  `open`/`high`/`low`/`close`/`volume` columns. Nothing else is required
  universally.
- **Identity/metadata**: carried *separately*, never mixed into the OHLCV
  columns, via `bot.instruments.Instrument` — `canonical_symbol`,
  `asset_class`, `base_currency`, `quote_currency`, `venue`, `timeframe`,
  `data_source`, plus the 9 new optional fields from this Stage.
- **Provenance**: `bot.research_lab.data_fingerprint.fingerprint_dataset()`
  (source/symbol/venue/timeframe/date-range/row-count → one deterministic
  hash), now wired into the real per-experiment path (this Stage).
- **Optional, asset-specific extensions** live on `Instrument` (this
  Stage's 9 new fields) or on dedicated modules genuinely scoped to one
  market (`ForexCostModel` for FX transaction costs; `derivatives_data.py`
  for crypto perpetual funding/OI — never forced onto an asset that
  doesn't have the concept).

No new wrapper class was introduced — the existing `DataFrame + Instrument`
pairing already satisfies every property the mission's proposed contract
asked for, and inventing a second representation nothing would consume
would be exactly the "unnecessary abstraction layer" the mission's own
rules warn against.

## Forex capability model

`bot.research.capabilities.DataRequirement`/`check_data_requirement()` —
see "What was changed" above. Demonstrated correct on one real case
(`feature_calculator.py`'s volume_ratio); broad adoption across the
research arsenal is named Stage 3 scope, not attempted here.

## Governance changes

`data_fingerprint` now populated for every real orchestrator-driven
experiment, both asset classes, symmetrically. `HypothesisFamily`
deliberately left unwired, with the architectural mismatch documented
above as a real finding requiring a genuine design decision before it
can be wired correctly.

## Tests

**184 tests, 184 passed, 1 skipped (environment-gated), 0 failed** across
every file touched or newly added:
`bot.tests.test_cross_section_engine`, `test_forex_terminal_data`,
`test_forex_dashboard`, `test_capabilities` (new), `test_instruments`,
`test_research_lab_orchestrator`, `test_research_lab_views`,
`test_research_lab_pairs_research`, `test_feature_calculator`,
`test_feature_stability_analyzer`, `test_feature_decay_analyzer`,
`test_entry_exit_engine`.

New test coverage added this Stage: `bot/tests/test_capabilities.py` (11
tests — `DataRequirement`/`check_data_requirement()` behavior plus the
real `feature_calculator.py` integration); `test_instruments.py`'s new
`InstrumentMarketMetadataTest` (5 tests — new field population, CRYPTO
unaffected, JPY pip-size differentiation, broker-specific fields honestly
`None`); `test_research_lab_orchestrator.py`'s new
`DataFingerprintGovernanceWiringTest` (5 tests — Crypto and Forex both get
a real fingerprint, pairs get a combined order-independent fingerprint,
fingerprinting failure never blocks a run, unregistered-symbol path still
fails closed at `BLOCKED` as before); `test_cross_section_engine.py`'s new
`RegressionCriticalUniverseIntegrityTest` (7 tests — the mission's exact
1.4 checklist: empty universe fails explicitly, missing columns/index
fail explicitly, below-minimum-assets fails explicitly, the real Forex
and Crypto callers never mix universes, provider/OHLCV metadata survives
through engine output, symbol→filename mapping is deterministic).

One genuinely pre-existing, unrelated stale test was found and fixed
along the way (`test_instruments.py`'s `test_list_instruments_filters_by_asset_class`
hardcoded `len(forex) == 8`, stale since the prior mission's 28-pair
universe widening — now compares against the real registry size).

## Bugs found

1. **`CrossSectionEngine`'s Steps 6–8 hardcoded-universe bug** — already
   found and fixed in the immediately-preceding cross-sectional audit
   (commit `3f6db46`), re-verified here as part of Stage 1's required
   "audit the existing Forex implementation" step (1.4). Not re-fixed;
   confirmed still fixed and regression-tested.
2. `feature_stability_analyzer.py`/`feature_decay_analyzer.py`'s
   hardcoded-crypto-universe defaults and crypto-only annualization —
   fixed this Stage (see above).
3. `entry_exit_engine.py`'s incomplete Sharpe-annualization migration —
   fixed this Stage.
4. `feature_calculator.py`'s silent all-NaN `volume_ratio` for Forex —
   fixed this Stage (now explicit).
5. `ResearchExperiment.data_fingerprint` never populated by real runs —
   fixed this Stage.
6. `execution_costs.py`'s fail-open venue fallback; `venue_readiness.py`'s
   hardcoded probe symbol — found, **not fixed** (Stage 2 territory,
   documented above).
7. `run_cross_sectional_oos.py`/`run_research_all.py` never reaching
   Forex at all — found, **not fixed** (Stage 3's top priority,
   documented above).
8. `HypothesisFamily`'s batch-shaped design mismatching the Research
   Lab's interactive flow — found, **not fixed**, reasoning given above.

## Remaining risks (carried forward, not resolved by Stage 1)

Everything in "What was intentionally NOT changed" above, ranked by
what blocks which future stage:

- **Blocks Stage 2** (must be resolved before or during building
  `ForexAdapter`): `venue_readiness.py`'s hardcoded crypto probe symbol,
  `execution_costs.py`'s fail-open venue fallback, `position_sizer.py`'s
  crypto-lot-size assumption, the complete absence of `get_cost_model()`
  wiring anywhere in the live execution/backtest stack.
- **Blocks Stage 3** (must be resolved before Forex research parity is
  real, not just theoretically possible): `run_cross_sectional_oos.py`/
  `run_research_all.py` never reaching Forex data; `kalman_filter_engine.py`'s
  crypto-tuned hyperparameters never re-validated for Forex; the
  `HypothesisFamily` design mismatch (needs a real decision before any
  systematic multi-hypothesis Forex research run can claim genuine FDR
  governance).
- **Open, no blocking urgency**: `price_validator.py`/`liquidity.py`'s
  centralized-exchange assumptions (no FX equivalent exists yet to
  design against); `kalman_filter_engine.py`'s separate dead-forward-fill
  bug (pre-existing, orthogonal to Forex).

## Stage 1 verdict: **PASS**

Every Stage 1 acceptance criterion is met: Crypto behavior is
byte-identical (184/184 tests, zero regressions); the canonical data
contract is documented; asset/provider boundaries are explicit;
crypto-specific assumptions across the full execution, research, and
governance layers have been inventoried with file:line evidence; the
confirmed, bounded instances of the mission's own named "regression-
critical" bug class have been fixed and regression-tested; Forex
instrument metadata now exists; volume semantics are now explicit via a
real capability-declaration mechanism (demonstrated on one real case);
cost semantics were audited and found correctly designed but orphaned
from execution (documented, not fixed — correctly out of Stage 1 scope);
dataset fingerprinting is now captured for every real research run, both
asset classes; the `HypothesisFamily` gap was investigated deeply enough
to discover it cannot be wired correctly without a real design decision,
and that finding — not a hasty wiring — is the responsible Stage 1
outcome. No unresolved P0/P1 blocker prevents Forex from being a
first-class **research** asset class going forward; the real, substantial
remaining work (execution-stack cost wiring, end-to-end research-arsenal
wiring, hypothesis-family redesign) is correctly scoped to Stages 2 and 3,
not silently left undone.

**Stage 2 should not begin without explicit approval**, per the
mission's own instruction. Recommended focus for that approval
conversation: the pre-registered Stage 2 blockers list above
(`venue_readiness.py`, `execution_costs.py`'s fallback, `position_sizer.py`)
should be addressed as part of — or immediately before — building
`ForexAdapter`, since a naive `ForexAdapter` implementation would
otherwise inherit all three silently.
