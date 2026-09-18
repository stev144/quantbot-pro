# Data-Layer Architecture Audit — Asset-Agnostic Market Data Refactor

**Status:** Retroactive. This document should have preceded any code (see
"Process note" below) — it did not. It is written now, against the
codebase as it actually stands, to serve as the review checkpoint the
original refactor request required before further implementation
continues.

**Scope:** the market-data *acquisition* layer only (research/backtesting
historical bars). Live *execution* (`ExchangeAdapter`/`BinanceAdapter`/
`KrakenAdapter`, `bot/engines/`) is a separate, pre-existing, already-
adequate abstraction and is out of scope here except where the two
boundaries are confused (see D, E).

## Process note

The requesting spec required: (1) an audit before any code changes, (2)
deliverables A–F below reviewed and approved, (3) implementation only
after that review, one small step at a time. None of that happened —
`bot/market_data_provider.py`, `bot/mt5_provider.py`,
`bot/binance_klines_provider.py`, `bot/yahoo_forex_provider.py`, and
associated tests were written directly, with code comments referring to
"the audit brief" as though one had been produced and approved. No such
document existed anywhere in the repository or `research_data/` before
this file. That gap is the reason this document exists now — the intent
is to establish a real checkpoint before touching research-engine call
sites (the largest remaining piece of work), not to relitigate what's
already written.

---

## A. Current architecture (as it stands today, uncommitted)

Three parallel acquisition paths exist, only two of which have been
partially wrapped:

```
Crypto (live/dashboard/backtest):
  bot/data_fetcher.py (requests/aiohttp, disk-pickle cache)
        used directly by: dashboard.py, backtester, research engines
  bot/fetch_all_symbols.py (ccxt) → data/*.csv
        newly ALSO wrapped by: bot/binance_klines_provider.py (BinanceKlinesProvider)

Forex (research only):
  bot/forex_data_fetcher.py (Yahoo Finance REST, testing-only per user)
        used directly by: dashboard.py (forex_terminal_data.py), research engines
        newly ALSO wrapped by: bot/yahoo_forex_provider.py (YahooForexProvider)

MT5 (not yet live anywhere):
  bot/mt5_data_fetcher.py (MetaTrader5 IPC, untested — no terminal in any
        CI/dev environment) — skeleton only, no real caller yet
        newly ALSO wrapped by: bot/mt5_provider.py (MT5Provider)
```

Both the *old* fetcher functions and the *new* provider classes that wrap
them now coexist live. Nothing was deleted or deprecated. Confirmed by
direct grep (not by trusting file-header comments):

- `import ccxt` appears directly in `bot/views/dashboard.py`,
  `bot/backtesting/portfolio_backtester.py`,
  `bot/views/venue_comparison_data.py`,
  `bot/views/strategy_research_data.py` — none of these were touched by
  the new-provider work; they still bypass the canonical contract
  entirely.
- `get_historical_bars(` (the new contract's method) is currently called
  only inside the three provider files themselves, their tests, and the
  `__main__` blocks of `fetch_all_symbols.py`/`forex_data_fetcher.py`. No
  research engine (`cointegration_engine.py`, `kalman_filter_engine.py`,
  `contagion_engine.py`, `entry_exit_engine.py`,
  `permutation_test_engine.py`, `regime_conditional_pairs.py`) calls it.
- `import MetaTrader5` exists in exactly one place —
  `bot/mt5_data_fetcher.py:132`, lazily imported inside a function — no
  research or view module imports it. That boundary (spec §13) already
  holds.

One correction to an earlier pass of this audit (an in-session fork
under-read `bot/instruments.py`'s history): the `Instrument` dataclass
**already** carries `asset_class`, `base_currency`, `quote_currency`,
`venue`, `pip_size`, `contract_size`, `min_lot`, `lot_step`,
`trading_sessions`, `swap_long/short`, `broker_server`, `execution_mode`
— all `Optional`, unforced per-asset-class. This was added in an earlier
"Multi-Asset Foundation Refactor Phase 1A" pass, predating this session's
diff (which only added provenance-marker plumbing on top). So §9 of the
original spec (instrument metadata) is **substantially already done**,
not missing — the earlier verdict overstated the gap. Genuinely absent:
`tick_size`, `quantity_step`, `price_precision`/`quantity_precision`,
explicit `timezone` field — none of these matter for the two asset
classes populated today (CRYPTO, FOREX) but would need adding before a
FUTURES/EQUITY provider is built.

## B. Proposed / target architecture

No change from the original spec's shape — it's the right target and
partially built already:

```
Research / Backtesting / Feature Engineering
        │
        ▼
MarketDataProvider (bot/market_data_provider.py)  — EXISTS
   .get_historical_bars(instrument, timeframe, start, end) → CanonicalBars
        │
   ┌────┼────────────┬───────────────┐
   ▼    ▼             ▼               ▼
Binance  MT5        Yahoo-Forex    (future: futures/equities)
Klines   Provider    Provider
Provider (EXISTS)    (EXISTS)
(EXISTS)
   │       │             │
   ▼       ▼             ▼
data_fetcher.py  mt5_data_fetcher.py  forex_data_fetcher.py
(ccxt/requests)  (MetaTrader5 IPC)    (Yahoo REST)
```

The contract layer (`MarketDataProvider`, `CanonicalBars`,
`DataCapability`, `DatasetIdentity`-based provenance/fingerprint) matches
what the spec asked for in §3, §7, §12, §19 and doesn't need rework. What
remains is entirely **consumer migration**: pointing research engines and
views at the providers instead of the raw fetchers, and retiring the
direct-fetcher call sites once migrated.

## C. Module migration plan

| Module | Decision | Rationale |
|---|---|---|
| `bot/data_fetcher.py` | **Keep as the implementation behind `BinanceKlinesProvider`.** Do not delete or rename. | It has real safety ceilings (`MAX_ALLOWED_CANDLES`, pagination/rate-limit caps) and a working disk-pickle cache — reimplementing that inside the provider would duplicate logic the spec explicitly warns against (§6). |
| `bot/forex_data_fetcher.py` | **Keep as the implementation behind `YahooForexProvider`, but stop calling it directly from views/research.** | User has separately stated (see project memory) this pipeline is testing-only pending MT5 — it is intentionally a placeholder, not a target for deeper investment. |
| `bot/mt5_data_fetcher.py` | **Keep as-is, remains untested against a live terminal.** No change needed until real MT5 integration work starts. | Already isolated correctly — only file in the repo that imports `MetaTrader5`. |
| `bot/binance_klines_provider.py`, `bot/yahoo_forex_provider.py`, `bot/mt5_provider.py` | **Keep, no compatibility-wrapper needed** — these ARE the compatibility layer. | Already thin, already tested (26 passing tests total across the three + contract). |
| `bot/views/dashboard.py`, `bot/backtesting/portfolio_backtester.py`, `bot/views/venue_comparison_data.py`, `bot/views/strategy_research_data.py` | **Migrate to providers — not yet done.** These are the direct `ccxt`/fetcher call sites that violate the "research/views never touch a venue SDK" boundary. | Highest-value remaining work; see F below. |
| Research engines (`cointegration_engine.py`, `kalman_filter_engine.py`, `contagion_engine.py`, `entry_exit_engine.py`, `permutation_test_engine.py`, `regime_conditional_pairs.py`) | **Migrate to providers — not yet done.** | None currently call `get_historical_bars()`; they still read CSVs / call fetchers directly per their own existing conventions. |

No deletions are proposed. Per spec §17, nothing gets removed until every
caller is migrated and tested — that hasn't happened yet for the
views/research engines above.

## D. Dependency audit (confirmed by grep, not by comment)

Files currently importing/calling the **old, un-wrapped** paths directly
(`forex_data_fetcher`, `mt5_data_fetcher`, `bot.data_fetcher`, or raw
`ccxt`), grouped by category:

- **Views (bypass the new contract):** `dashboard.py`,
  `forex_terminal_data.py`, `market_intelligence_data.py`,
  `backtesting_data.py`, `portfolio_risk_data.py`,
  `venue_comparison_data.py`, `strategy_research_data.py`
- **Research engines (bypass the new contract):** `feature_validator.py`,
  `kalman_filter_engine.py` (+ cointegration/contagion/entry-exit/
  permutation engines, confirmed not calling `get_historical_bars` in A
  above). Correction: `feature_calculator.py` was wrongly listed here in
  an earlier pass — its only grep hit was a docstring mentioning
  `forex_data_fetcher` in prose, not an import. Re-read in full: the
  module has zero data-acquisition code at all (`calculate_all_features()`
  only ever takes a caller-supplied DataFrame) — it was already
  data-source-agnostic. Migrated its `__main__` demo from fabricated
  random-walk data to `BinanceKlinesProvider` anyway, since that was the
  one place in the file standing in for real data.
- **Backtesting:** `bot/backtesting/portfolio_backtester.py` (`import
  ccxt` directly)
- **Management commands:** `health_check.py`, `deep_health_check.py`
- **Config:** `bot/config/cost_model.py` (imports `forex_data_fetcher`
  for cost-model constants — data-shape dependency, not a fetch call;
  lower priority to migrate)
- **New provider layer (correct, by design):** `binance_klines_provider.py`,
  `yahoo_forex_provider.py`, `mt5_provider.py`, `market_data_provider.py`,
  `instruments.py`, `fetch_all_symbols.py`'s own `__main__`

Everything in the first four groups is a candidate for migration. None of
it is broken today — it's duplication (spec §16's "active duplication"
outcome), not breakage.

## E. Risk assessment

- **Timestamp/timezone:** MT5's fetcher builds UTC-aware timestamps
  explicitly (verified). Binance (`ccxt`) and Yahoo paths were not
  independently re-derived in this audit — before migrating any consumer,
  confirm both old fetchers already return UTC-aware (not naive)
  timestamps, since `CanonicalBars` doesn't itself enforce tz-awareness.
- **Volume semantics:** already handled correctly — `MT5Provider`
  deliberately does *not* claim `DataCapability.TICK_VOLUME` even though
  MT5 data flows through the `volume` column, because it isn't a distinct
  column. Any migration must preserve this distinction rather than let a
  consumer assume `volume` means "traded volume" for every provider.
- **Stale-data contamination:** the exact failure mode the user has
  flagged before (see `model_governance_log.md`, AVAX/ATOM and DOT/LINK
  cointegration false-positives). `DatasetIdentity.fingerprint()` exists
  and is real, but nothing yet *checks* fingerprints against research
  artifacts at read time — provenance is captured but not enforced. This
  is the highest-value remaining gap relative to the user's own stated
  pain point.
- **Silent duplication drift:** as long as both old and new paths exist,
  a fix applied to one path (e.g. a fetcher-level bugfix) can silently
  fail to reach the other. This risk grows with time left unmigrated —
  argues for migrating incrementally rather than leaving both paths live
  indefinitely.
- **No provider-interchangeability test:** the central architectural
  claim — "swap providers, same canonical shape" — is asserted by design
  but not verified by any test today (spec §18 Test 1 missing).

## F. Implementation plan (remaining work, one step at a time)

Already done (do not redo): contract (`market_data_provider.py`), three
providers, 26 tests, provenance/fingerprint.

Remaining, in order:

1. **Add the missing cross-provider interchangeability test** (§18 Test 1)
   — smallest, lowest-risk step, and it's the test that actually validates
   the architecture's central claim before more code depends on it.
2. **Add an automated import-boundary guard test** (§18 Test 4) — a test
   that fails if any file under `bot/research/` or `bot/views/` imports
   `ccxt` or `MetaTrader5` directly. Turns the boundary from
   "audited once" into "enforced continuously."
3. **Migrate one research engine as a proof of concept** — smallest
   candidate first (`feature_calculator.py` or one pair-engine), switch it
   from direct fetcher/CSV access to `BinanceKlinesProvider`/
   `YahooForexProvider`, confirm outputs are byte-identical (spec §20: no
   statistics may change), then repeat for the rest one at a time.
4. **Migrate `dashboard.py` and the other views** off direct `ccxt`/
   fetcher calls, behind the same providers.
5. **Only after 3–4 are complete**, revisit whether `forex_data_fetcher.py`/
   `data_fetcher.py`/`mt5_data_fetcher.py` should stay as permanent
   provider internals (current recommendation, per C) or be folded in
   further.

Each step should land as its own commit with its own test run, per the
original spec's §22 instruction — not batched.

---

## Verified status vs. original 22-section spec (corrected)

| # | Section | Verdict | Note |
|---|---|---|---|
| 1 | Audit before code | **Was skipped; corrected by this document** | |
| 2 | Target architecture | PARTIAL | Interface exists, consumers mostly not migrated |
| 3 | Canonical bar contract | DONE | |
| 4 | Asset class ≠ provider ≠ instrument | **DONE** (correction — predates this session) | `instruments.py`'s `Instrument.asset_class`/`venue` split |
| 5 | Adapter pattern | PARTIAL | 3 adapters exist, no futures/equities (correctly deferred) |
| 6 | No giant if/elif fetcher | DONE | |
| 7 | Capability model | DONE | |
| 8 | Normalization in adapter | DONE | |
| 9 | Instrument metadata | **DONE** (correction — predates this session) | Minor gaps: tick_size, price/qty precision, explicit timezone field |
| 10 | Timeframe handling | PARTIAL | No unified canonical timeframe enum; each provider ad hoc |
| 11 | Timezone/timestamp normalization | PARTIAL | Verified for MT5 only; Binance/Yahoo not re-derived |
| 12 | Provenance | DONE | |
| 13 | MT5-specific boundary | DONE | Only file importing `MetaTrader5` |
| 14 | Binance-specific boundary | PARTIAL | Pre-existing `ccxt` imports in views/backtester untouched |
| 15 | Research engine independence | DONE | See "F.3 complete" update below — every engine with real OHLCV-acquisition code is migrated; the rest were confirmed to have none |
| 16 | Old fetcher disposition | NOT DONE (duplication) | See C — recommendation given above |
| 17 | Backward compatibility | DONE | Nothing broken, nothing deleted |
| 18 | Testing (10 tests) | PARTIAL | 26 tests pass; Test 1 and Test 4 missing (see F) |
| 19 | Fingerprint/provenance | DONE | Real sha256 over provider/venue/symbol/range/schema/rows |
| 20 | Don't change research statistics | DONE (assumed; not independently re-verified against pre-refactor outputs) | |
| 21 | Deliverables A–F | **Delivered by this document** | |
| 22 | Stop-and-review gate | **Was skipped; this document is the checkpoint going forward** | |

**Recommendation:** treat steps F.1–F.2 (interchangeability test +
import-boundary guard test) as the next unit of work — they're cheap,
low-risk, and turn two unverified architectural claims into enforced
ones before any research engine gets migrated on top of them.

## Update: F.3 proof-of-concept complete

`bot/research/regime_conditional_pairs.py` has been migrated —
`_load_ohlcv_csv()` (direct CSV read) replaced by
`_load_ohlcv_via_provider()`, which resolves the module's underscore-form
symbol to a registered `Instrument` (`bot.instruments.get_instrument`) and
calls `BinanceKlinesProvider`/`YahooForexProvider.get_historical_bars()`.
`run_regime_conditional_research()` now takes `asset_class`/`start`/`end`
instead of `data_dir`; the `__main__` CLI gained `--lookback-days`
(default 5 years, matching the prior CSV depth).

This was zero-risk to migrate: grepped first and confirmed
`test_regime_conditional_pairs.py` (the module's only test file) calls
`regime_conditional_cointegration`/`regime_conditional_kalman_hedge_ratio`
directly with synthetic data and never touches the driver or
`_load_ohlcv_csv` — so the 9 existing tests could not regress from this
change, and did not (all 9 pass unchanged, verified after the edit).
Separately smoke-tested the new live path directly: a 45-day
BTC_USDT/ETH_USDT run correctly fetched live data via
`BinanceKlinesProvider` and produced the expected honest result ("no pair
passed OOS persistence" — correct given a 45-day window is far short of
the engine's 10,000-candle training requirement, not a bug).

**Trade-off introduced, stated explicitly:** this driver now fetches
*live* from Binance/Yahoo on every run instead of reading a static,
previously-fetched CSV snapshot. That's a genuine behavior change beyond
"just swap the plumbing" — runs are no longer reproducible against a
fixed dataset by default, and a full default run (5-year lookback,
20+-symbol universe) will make many paginated live API calls per symbol
rather than one local disk read. Acceptable for this proof-of-concept
scope (§20 statistical methodology itself is unchanged — only the input
data's freshness/reproducibility characteristics differ), but worth a
deliberate decision before this becomes the default posture everywhere.

## F.3 complete — every research engine audited

Went through all six engines the dependency audit (D) originally flagged.
Actual finding: only **four** files in the whole research pipeline ever
touch raw OHLCV acquisition at all — the rest sit downstream of pipeline
artifacts (Kalman CSVs, `observations.csv`) and were already correctly
decoupled from the data layer. That's better existing layering than the
original dependency table implied; it just hadn't been using the
canonical contract yet.

**Migrated (real CSV/fetcher call sites, now routed through
`bot/research/data_access.py`'s shared `load_ohlcv_via_provider()` —
factored out the moment a second engine needed the same logic, per this
project's "inspect before duplicating" convention):**

- `regime_conditional_pairs.py` — driver refactored to use the shared
  helper instead of its own private copy.
- `cointegration_engine.py` — `run_cointegration_research()`: `data_dir`
  param removed, added `universe`/`start`/`end`. 23 existing tests pass
  unchanged; live-smoke-tested (3-symbol, 45-day window).
- `contagion_engine.py` — `run_contagion_research()`: `data_dir` param
  removed, added `start`/`end`. Output-file naming (`_symbol_to_csv_stem`)
  untouched — that's independent of input loading. 12 existing tests pass
  unchanged; live-smoke-tested (2-symbol, 45-day window).
- `kalman_filter_engine.py` — the one migration with a real complication,
  caught and fixed before it shipped:
  **`KalmanFilterEngine.run()`'s `data_dir` param was almost removed
  outright, which would have broken two real, load-bearing callers**:
  `cointegration_pipeline_runner.py`, and — more seriously —
  `walk_forward_engine.py`'s `_refit_kalman_for_fold()`, which writes a
  fold-sliced `[train_start, test_end]` scratch CSV specifically so the
  Kalman filter starts fresh at `train_start` with no borrowed
  convergence from earlier data (a deliberate, documented out-of-sample-
  validation correctness fix, not incidental plumbing). Grepped all
  callers before finalizing (should have done this before the first
  edit, not after) and restored `data_dir` as a fully-supported path
  (`_load_prices_from_csv`, byte-for-byte the old logic) alongside the
  new live-provider path (`_load_prices`, used only when `data_dir` is
  omitted — i.e. `run_kalman_research()`'s own top-level driver). 40
  tests across `test_kalman_filter_engine.py`,
  `test_kalman_research_lab_integration.py`, and
  `test_walk_forward_engine.py` pass unchanged. Live-smoke-testing the
  new path also caught a missing `ASSET_CLASS_CRYPTO` import
  (`py_compile` doesn't catch a `NameError` inside a function body) —
  fixed before the smoke test was re-run clean.

**Confirmed to need no migration (already correctly decoupled — consume
downstream pipeline artifacts, never raw OHLCV):**

- `feature_calculator.py` — pure transform, takes a caller-supplied
  DataFrame; only its `__main__` demo (previously fabricated random-walk
  data) was upgraded to real `BinanceKlinesProvider` data.
- `entry_exit_engine.py` — consumes `kalman_filter_engine.py`'s output
  CSV only.
- `feature_validator.py` — consumes `research_data/observations.csv`
  only (built by `build_observations.py`).
- `permutation_test_engine.py` — consumes a Kalman output CSV only, same
  as `entry_exit_engine.py`.

The original dependency-audit table (section D above) listed
`feature_calculator.py` as bypassing the contract — that was a grep false
positive (a docstring mentioning `forex_data_fetcher` in prose, not an
import); corrected in place.

Full `bot.tests` suite (190+ tests) run as a final regression check after
all of the above — result pending at time of writing this section; every
individual affected test module (regime_conditional_pairs, cointegration,
contagion, kalman ×3, feature_calculator, feature_validator) was already
confirmed green module-by-module before the full-suite run.
