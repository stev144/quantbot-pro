# Quant Bot Pro — Forensic Audit & Deep Health Check Hardening
**Scoped Pass 1 — Final Report**
Generated: 2026-09-03

## How to read this report

This is Pass 1 of a mission that, taken literally (34 sections, full
module-by-module classification, a complete health-check rebuild), is
weeks of work. Attempting to fake full coverage in one pass would
violate the mission's own explicit instruction to be brutally honest
and not optimize this report for making the project look impressive.
So: everything below is backed by evidence actually gathered and code
actually changed and tested in this pass. Everything NOT covered is
named explicitly in the "Deferred" section at the end — nothing is
implied as done by omission.

---

## 1. Executive verdict

- **Live-readiness: NOT LIVE SAFE.** No portfolio-level risk view exists
  anywhere in the codebase; the only real kill-switch (`DrawdownGuard`)
  is in-memory (not restart-safe) and guards new entries only, not
  exits; nothing halts the live loop on repeated order failures or
  stale market data. One real safety-gate bug (reconciliation always
  certifying "clean") was found and fixed this pass, but the fix has
  not been battle-tested against a real exchange connection — no API
  keys are configured on this machine, so the system is currently
  `DRY_RUN_READY`, not `LIVE_READY`, which is the correct and safer
  state to be in today.
- **Research-readiness: no viable trading edge found yet.** Cointegration
  research (100-asset universe, 4,950 pairs, 17 FDR-validated
  candidates that reached OOS testing) produced `NO_SURVIVORS`.
  Cross-sectional `cs_zscore` produced `NO_SURVIVORS`. Cross-sectional
  `cs_reversal_signal` (mean reversion) was flagged `any_survivor:
  true` by the automated FDR pipeline, but on inspection this is a
  narrow statistical artifact, not a tradeable edge — see §5.
- **Code health: real defects found and fixed, not merely diagnosed.**
  Six concrete P0/P1 defects were fixed and regression-tested this
  pass (§3). The health-check tool itself had a real defect (a blind
  reflection layer capable of firing a live, signed exchange API call)
  that is now fixed and regression-guarded.
- **Test suite: 955 tests, 3 failures found, 1 was a real regression
  (fixed), 2 were data-staleness artifacts, not code bugs** (§4).

---

## 2. Architecture map & evidence (Pass 1 recon)

**Universe dynamism.** The real chain
(`universe_selector.py` → `data/universe_selection.json` →
`fetch_all_symbols.py` → `bot/instruments.py` → every research engine)
is genuinely dynamic. Three hardcoded fallback symbol lists were found
reachable in real, non-test runs, silently substituting a stale ~5-7
coin universe instead of the ~100-coin dynamic one — fixed, see §3.

**Dataset fingerprinting.** `bot/research_lab/data_fingerprint.py` is
real and correctly built, but was dormant — no real research entry
point ever called it. Wired into the one entry point this session
built and controls (`run_cross_sectional_oos.py`); the other 9
standalone engines remain unwired (named as a deferred item, not
silently fixed everywhere).

**Research governance boundary.** `HypothesisFamily`/
`ResearchExperiment`'s freeze/append-only enforcement (verified by
reading the actual `save()`/`delete()` overrides) is real, but scoped
only to the Research Lab UI's orchestrated flow. The six standalone
scripts that produced 100% of this session's real research output —
`cointegration_engine.py`, `permutation_test_engine.py`,
`walk_forward_engine.py`, `cross_sectional_permutation_test.py`,
`run_cross_sectional_oos.py`, `cointegration_pipeline_runner.py` —
never touch governance at all; they write plain JSON/CSV with no DB
row, no freeze, no FDR family declaration. This is a known, real gap,
now made **visible on every health-check run** via the new
governance-boundary check (§ Milestone B) instead of being silent.

**Kill-switch.** One real, wired mechanism: `DrawdownGuard`
(`bot/risk/drawdown_guard.py`), correctly called from `execute_signal()`
before every new entry. Documented, real limitations: in-memory only
(not restart-safe), guards new entries only (not exits), and — traced
through the actual call chain — nothing halts the live loop on repeated
order failures or stale market data; the "Bot pausing" log message on
rate-limiting is misleading and does not pause anything beyond a 120s
sleep-and-retry of the same loop. **Not fixed this pass** — a real
portfolio-risk/kill-switch layer is a multi-week build, named as a
follow-up, not attempted here.

**Portfolio risk.** `PositionTracker`/`PositionSizer`/`DrawdownGuard`
were built and reasoned about for exactly one symbol/one position at a
time per venue — confirmed by the code's own explicit comments. No
aggregate exposure view exists anywhere. Stated architectural boundary,
not a silent gap. Too large to close in this pass.

**Execution safety.** Duplicate-order prevention (in-memory +
DB-level `UniqueConstraint`) and retry-vs-double-fill handling are real
and well-designed. One real defect found and fixed: `reconcile_with_
exchange()` always set `is_reconciled = True` regardless of what it
detected — see §3, item 2.

**Security / DB / frontend / AI.** Middleware order correct; the
missing global auth gate is a deliberate design decision (public
dashboard), not an oversight; every `research_lab` view has
`@login_required`; every `bot/views/*.py` view is read-only (no
mutation, so no auth gap on mutation). `CLAUDE.md` had a stale,
incorrect security claim (fixed, §3 item 5). `ResearchExperiment.
student` used `on_delete=CASCADE`, silently bypassing the model's own
append-only guarantee (fixed, §3 item 4). Frontend verdict-display
honesty check: **clean** — no path found anywhere that shows "validated"
/"passed" language for a `REJECTED`/`INCONCLUSIVE` backend verdict.
AI/ML readiness: zero ML libraries, zero model-artifact-storage
pattern anywhere in the codebase.

---

## 3. Fixed defects (Milestone A — all 6 complete, tested)

1. **P0 — `deep_health_check.py` could fire a real live exchange call.**
   Layer 2's blind reflection reached `bot.core.bot_runner` during a
   real audit run this session and fired a signed Binance API call
   (safe only by luck — no real key configured on this machine) plus
   flooded real `aggTrades` traffic via `bot.engines.trade_data`.
   **Fix**: `LIVE_EXCHANGE_MODULE_PATTERNS` exclusion set, matched by
   exact module-path prefix. 5 regression tests confirm exclusion works
   and doesn't over-match unrelated modules (e.g. `price_validator`
   stays included).

2. **P0 — `reconcile_with_exchange()` false certification.** Always set
   `self.is_reconciled = True` regardless of a detected mismatch — the
   one hard safety gate the system relies on only certified
   "reconciliation ran," not "reconciliation found a clean state."
   **Fix**: tracks `reconciliation_clean` and sets `is_reconciled`
   accordingly; a detected mismatch or any exception now blocks
   trading. 7 regression tests with a fake-exchange stub. Deliberately
   did NOT gate on "untracked filled order" detection, since
   `open_positions` is always empty at call time — that check would
   have falsely flagged every account with real trading history on
   every startup. Caught during design, not after a failing test.

3. **P1 — 3 hardcoded fallback symbol lists.**
   `portfolio_backtester.py` (was a 7-symbol hardcoded default, now
   `symbols_for_asset_class(...)[:7]` — capped at 7 deliberately, since
   the real call chain is a synchronous Django view with a 1s
   rate-limit sleep per symbol; defaulting to the full ~100-coin
   universe here would risk multi-minute request timeouts).
   `backtesting_data.py` (was a 5-symbol `DEFAULT_SYMBOLS` constant,
   now dynamic, format-preserved). `feature_stability_analyzer.py`
   (was 7 hardcoded symbols, now the full dynamic universe — no
   synchronous request-path constraint here, so no cap needed).

4. **P1 — `ResearchExperiment.student` CASCADE.** Deleting a `User`
   silently wiped their entire "permanent, institutional" research
   history via Django's bulk cascade collector, which never calls a
   model's own overridden `delete()`. Changed to
   `on_delete=models.PROTECT`, migration generated and applied,
   regression test added proving both that deletion now raises AND
   that the experiment genuinely still exists afterward.

5. **P1 — stale `CLAUDE.md` security section.** Corrected a claim that
   `DEBUG=True`/hardcoded `SECRET_KEY` were in effect; actual
   `config/settings.py` is correctly hardened via `python-decouple`.
   Documentation-only.

6. **P1 — dataset fingerprinting dormancy.** Wired `fingerprint_dataset()`
   into `run_cross_sectional_oos.py`'s `run_cross_sectional_research()`
   — the one real cross-sectional orchestrator this session built and
   controls. Verified end-to-end (compile check, direct smoke test,
   2 new regression tests, full 72-test `test_oos_validator` +
   `test_cross_sectional_oos_evaluator` suite — zero regressions).
   Deliberately scoped narrow: the other 9 legacy standalone engines
   remain unwired (§6).

---

## 4. Milestone B — Deep Health Check redesign

Added, additively, on top of the existing Layer 1-4 + Drift structure
(nothing existing removed or altered):

- **`HealthFinding` dataclass** (component, check, severity, evidence,
  expected, actual, impact, remediation_status) and
  `GREEN`/`YELLOW`/`ORANGE`/`RED` severity constants.
- **`compute_overall_severity()`** — max-based, never averaged: one
  RED among fifty GREENs still reports RED overall. Unit-tested
  directly.
- **Mapping functions** translate existing Layer 2 FAIL, Layer 3
  missing-test, and Drift CRITICAL/WARNING results into findings
  (PASS/SKIP/OK produce no noise).
- **Four new evidence-based category checks**, each with a real
  regression-detection proof (not just "currently passes"):
  - `check_universe_dynamism()` — regression-guards fix #3 above.
  - `check_governance_boundary()` — standing YELLOW naming the 6
    ungoverned engines, makes a known gap visible every run.
  - `check_dataset_fingerprint_coverage()` — GREEN for
    `run_cross_sectional_oos.py`, YELLOW for the other 9.
  - `check_reconciliation_gate()` — regression-guards fix #2 above;
    proven (via monkeypatching) to actually catch a reverted
    always-`True` implementation, not just observe the current fixed
    state.
- Wired into a new additive "Layer 6: Structured Findings" section in
  `Command.handle()`, the console summary, and the JSON export.
- 19 new tests added (24 total in the file, up from 5); independently
  re-run and confirmed **24/24 pass**.
- **Explicitly not attempted**: portfolio/risk checks, execution/
  kill-switch checks beyond reconciliation, Academy checks, full
  AI/ML-readiness checks — no underlying subsystem exists yet for most
  of these.

---

## 5. Research-readiness detail

| Program | Result |
|---|---|
| Cointegration (100 assets, 4,950 pairs, 17 FDR-validated candidates reaching OOS) | **NO_SURVIVORS** |
| Cross-sectional `cs_zscore` (rebalance_frequency=24, realistic costs) | **NO_SURVIVORS** |
| Cross-sectional `cs_reversal_signal` (mean reversion) | Flagged `any_survivor: true` — see caveats below |

`cs_reversal_signal` detail, by top-k:

| top_k | Sharpe | Mean net return | Hit rate | Max DD | Cumulative cost | p-value | FDR pass |
|---|---|---|---|---|---|---|---|
| 1 | **+0.26** | +0.005% | 50.2% | 84.4% | 451% | 0.0323 | yes |
| 3 | -0.56 | -0.006% | 50.0% | 64.9% | 452% | 0.0323 | yes |
| 5 | -1.14 | -0.008% | 49.6% | 49.2% | 453% | 0.0323 | yes |
| 10 | -1.76 | -0.009% | 49.4% | 29.4% | 453% | 0.0323 | yes |

Two problems prevent this from being called a real finding:

1. **Statistical significance ≠ profitability.** For top_k=3/5/10 the
   real Sharpe ratio is negative — the strategy loses money — yet it
   still passes the permutation/FDR test, because the test only asks
   "is the real result distinguishable from randomly-shuffled labels,"
   and real losses happen to be smaller than shuffled losses. Only
   top_k=1 has a positive Sharpe, and it's weak (0.26) with an 84%
   max drawdown — not viable as a strategy.
2. **Every config hit the exact same p-value (0.0323 = 1/31)** — the
   floor resolution at `n_permutations=30`. This is a precision
   ceiling, not four independently-confirmed significant results.
   Before this candidate goes anywhere near the next rung of the
   validation ladder, it needs a re-run at higher `n_permutations`
   (200+) for a real p-value estimate.

Net honest read: **no economically viable edge has been found yet on
OHLCV-only data**, across two independent research programs.

---

## 6. Milestone C — Full regression matrix

`manage.py test bot` (run once the live research job finished, to
avoid CPU contention): **955 tests, 2047.6s, 3 failures, 2 skipped.**

| Test | Root cause | Disposition |
|---|---|---|
| `test_not_implemented_capability_is_coming_soon_regardless_of_subscription` | Real regression: `cross_sectional_research` was promoted to `IMPLEMENTED_AND_READY` earlier this session (correctly — it's now real and tested), but this test in `test_research_lab_entitlements.py` was never updated to match; it now correctly shows `LOCKED`/`AVAILABLE` instead of `COMING_SOON`. | **Fixed** — test swapped to `feature_stability_research`, a capability still genuinely `NOT_IMPLEMENTED`. Reverified: 21/21 pass in that file. |
| `test_every_tracked_symbols_data_is_within_freshness_window` | Environmental: 3 symbols (MBOX, POND, ARDR) have real cached OHLCV data 55-76 days stale. Not caused by any code change this pass. | **Not fixed** — would require a live `fetch_all_symbols.py` run (real exchange API calls), which was not authorized this pass. Flagged for the user to run when convenient. |
| `test_real_1h_crypto_result_is_byte_identical_to_pre_fix_baseline` | Environmental: this test locks an exact statistic computed from real `data/BTC_USDT_1h.csv`, with a fixed seed. That file was refreshed to Sep 2 21:04 (43,801 rows, through 17:00 UTC) during other work this session — new real candles shift the real/shuffled statistics under a fixed seed. Not a logic bug. | **Not fixed** — flagged as a systemic test-design issue: any test that hardcodes an exact statistic from a live-refreshed CSV will drift every time that data is updated. Recommend either freezing a committed synthetic dataset for this specific test, or loosening the tolerance, as a follow-up. |

The full 955-test suite was not re-run byte-for-byte after the
one-line, test-only entitlements fix (extremely low regression risk,
no production code touched); the affected file was re-run in isolation
and passed cleanly.

---

## 7. Deferred in this pass (named, not silently skipped)

- A real portfolio-level exposure/risk aggregation layer.
- A kill-switch that halts on repeated order failures or stale market
  data (currently nothing does).
- Restart-safe (persisted) `DrawdownGuard` state.
- Retrofitting `ResearchExperiment`/`HypothesisFamily` governance into
  the 6 standalone research engines named in §2/§4.
- Retrofitting dataset fingerprinting into the other 9 standalone
  research engines (only `run_cross_sectional_oos.py` is wired).
- Full sections of the original mission's health-check spec:
  portfolio/risk checks, execution/kill-switch checks beyond
  reconciliation, Academy checks, full AI/ML-readiness checks — no
  underlying subsystem exists yet for most of these.
- Full API/service-layer audit, full Academy/Research-Lab UI audit
  beyond verdict-display honesty, full observability audit, and a
  module-by-module classification of every file in the codebase.
- A live-exchange-connected battle test of the fixed reconciliation
  gate (no API keys are configured on this machine).
- Re-running `cs_reversal_signal` at higher `n_permutations` to get a
  real (non-floor) p-value before drawing any further conclusion from
  it.
- A fix for the two environmental test failures in §6 (data refresh
  and a live-data-dependent baseline test).

Each of the above is real, each is bigger than a safe minimal patch,
and each belongs in a dedicated follow-up mission.
