# Quant Bot Pro: Post-Audit Completion Report (2026-10-02)

**Base:** branch `stage2d-revb` @ `565e55d`. All changes are uncommitted.
**Programme:** master remediation prompt, executed **in order**. Phase 1 has been worked to its decision point. **Phases 2–7 have not started**, because Phase 1 needs one explicit owner decision (§5) before its verdict. The prompt says to stop at such a point rather than guess.

Audit vocabulary used throughout:

| Term | Meaning |
|---|---|
| **Implemented** | Code exists |
| **Tested** | Automated tests exercise it |
| **Verified** | Independently checked: known-answer or synthetic data, mutation-checked |
| **Economically justified** | Has a stated trading rationale |
| **Statistically supported** | Evidence survives the appropriate test |
| **Research hypothesis** | Plausible, but not demonstrated |
| **Unknown** | Cannot be established from the evidence |
| **Blocked** | Needs a decision or an upstream fix |

---

## 1. Executive summary

| Area | Status |
|---|---|
| Phase 1: pairs live safety (A1–A6, plus 4 + 1 review defects) | Implemented · Tested · **Verified** (mutation-checked) |
| Regression vs clean `HEAD` | 1 Phase 1 regression found and **fixed**. 85 failures are identical on `HEAD` (§4). The post-fix full re-run is **complete: 0 regressions** (§4.4). |
| A2 strategy equity | Correctness defects fixed. **Owner decision taken: liquidation-value marking, implemented** (§5) |
| Journal-mode separation | Implemented · Tested (migration 0014 + writers + guard + 3 dashboards) (§6) |
| Migrations 0012 / 0013 / 0014 | **Already applied** to the local DB at 17:16:03 by a process outside this audit. **Owner decision: Railway now verifies, never applies** (§7) |
| Pairs live safety verdict | **READY for continued DRY_RUN soak.** Live capital: **BLOCKED** (§8) |
| DCA (Phase 2) | **Implemented · Tested · Verified** (§9); real-data tail-risk review done; full null suite not run (needs a research decision) |
| Main bot (3), research statistics (4), backfill (5), feature-data swap (6), Stage 2D (7) | **Not started** (§10–§14) |

**Nothing traded. No exchange contact** from code or tests: every run refused non-loopback connections and logged each attempt. **No scan, no holdout, no Stage 2D step.** DCA was modified **in Phase 2 only, after Phase 1 closed**, and remains research-only.

---

## 2. Current architecture status (what this report changes)

| Layer | Change in this programme |
|---|---|
| `bot/pairs/execution.py` | A1–A4; review fixes R1–R4; regression fix R5; journal-mode–aware strategy equity |
| `bot/pairs/margin_adapter.py` | A4 touch-price dry-run fill model |
| `bot/pairs/trade_logger.py` | Fill / borrow labels, incidents, execution mode, provenance, `closed_at` |
| `bot/journal/models.py` + migrations 0013 and 0014 | `ExecutionIncident`; `fill_model`, `borrow_cost_status`; `execution_mode`, `methodology_version`, `protocol_hash`, `data_version`, `closed_at` |
| `bot/journal/trade_logger.py`, `bot/engines/execution_engine.py` | Main-bot journal rows now carry LIVE / DRY_RUN |
| `bot/core/pairs_bot_runner.py` | Migration gate; incident-based restart disable; guard heartbeat; provenance on the logger |
| `bot/views/terminal_data.py`, `forex_terminal_data.py`, `pairs_pilot_data.py` (+ 4 templates) | Mode-separated P&L |
| `bot/views/feature_intelligence*.py`, `templates/feature_correlation.html` | Correlation-matrix upgrade (§13) |

**Phase 2:** `bot/research/dca_engine.py` (B1–B8), `bot/tests/test_dca_engine.py` (pinned to `LEGACY_CLOSE`), `bot/tests/test_dca_phase2.py` (new), `DCA_RESEARCH_PROTOCOL_2026-10-02.md` (new); `railway.toml` (verify-only migrations).

**Not changed:** `bot/pairs/pnl.py` (canonical), `bot/risk/drawdown_guard.py`, every Stage 2D module, every other research engine, and all risk and cost constants.

---

## 3. Phase 1: pairs live status

Details are in `POST_AUDIT_PHASE1_PAIRS_SAFETY_REPORT.md`. In summary:

- **A1 naked leg:** every exception routed; unwind from authoritative account state; NO_EXPOSURE / UNWOUND_CONFIRMED / MANUAL_REVIEW_REQUIRED stored durably; restart-durable disable; never a retry.
- **A2 drawdown guard:** enforced before any transfer or order (§5).
- **A3 exit minimums:** refused before any order; position preserved.
- **A4 dry-run fills:** side-correct touch + declared slippage; borrow `UNAVAILABLE`, never a silent zero.
- **A5 migrations:** the runner refuses to start on a pending or unverifiable schema.
- **A6 reconciliation:** verified by existing tests. Backfill deferred.

**Review defects found in the Phase 1 code itself, all fixed and mutation-checked:**

| # | Defect | Fix |
|---|---|---|
| R1 | Guard fail-open at equity ≤ 0 | Block explicitly when equity ≤ 0 |
| R2 | Peak updated per leg | Peak points only at pair completion |
| R3 | Borrowed-but-unsold short leg market-sold on unwind | Repaid first |
| R4 | "Unwound" incident while a leg stayed OPEN | Forces MANUAL_REVIEW |
| R5 (§4) | Missing capital raised an exception | Fails closed |

**Tests:** `test_pairs_live_safety` now has **50** tests: 33 Phase 1 and earlier review regressions, 9 journal-mode/A2, 3 dashboard separation, 5 liquidation-mark. `test_fixed_fraction_execution` has +1 test. (An earlier draft of this report said 56; that was a miscount.)

---

## 4. Regression comparison (exact test names)

### 4.1 Method
- Both trees ran the **whole suite** in 4 serial shards each:
  - the working tree;
  - a clean `HEAD` worktree, with the untracked data directories junction-linked read-only so both trees see identical inputs.
- Every non-loopback connection was refused, and each attempt was logged.
  - Blocked attempts were identical in both trees: `api.binance.com` 2,125 · `api.kraken.com` 29 · `data.binance.vision` 12 · `query1.finance.yahoo.com` 2.
- Failure lists come from unittest's end-of-run `FAIL:` / `ERROR:` headers, matched by full dotted test name.

### 4.2 Totals

| | Working tree | Clean `HEAD` |
|---|---|---|
| Tests run | 1,571 | 1,538 (difference = the 33 new Phase 1 tests) |
| Fail + error | 86 | 85 |

### 4.3 Matrix

| Class | Count | Evidence |
|---|---|---|
| **Phase 1 regression** | **1, fixed** | `test_fixed_fraction_execution.EntryQuantityTests.test_open_pair_trade_refuses_before_any_transfer`: ERROR on the working tree, PASS on `HEAD`. Cause: the new pre-entry guard read `pair_config.capital_usdt` before the executability check, and the test's stand-in config lacked it. **Code fix (R5):** missing capital → guard fails **closed**, never raises. **Test fix:** the fixture now carries `capital_usdt` (the real `PairConfig` always does). The guard is stubbed *explicitly* in that one test, and the test asserts the refusal really is the minQty one; otherwise it would pass for the wrong reason. Plus a new test showing a capital-less config fails closed. |
| Pre-existing: direct network block | 58 | Fails identically on `HEAD`; error text names the blocked host |
| Pre-existing: indirect network block | 22 | Fails identically on `HEAD`; the blocked call is caught and surfaces as `no price for BTC/USDT`, `network_error`, empty klines, `None` OHLCV or `NOT_READY` (`test_binance_adapter`, `test_kraken_adapter`, `test_execution_result`, `test_order_intent`, `test_bot_runner_venue_sessions`, `test_binance_klines_provider`, `test_venue_comparison`, `test_venue_readiness`, `test_execution_engine_adapter_wiring.test_exit_updates_trade_record`) |
| Pre-existing: data / environment, **not network** | 5 | Fail identically on `HEAD`. **Real open items** (see below). |

The 5 data / environment failures:
1. `test_data_freshness`: local candles are 7.6 days old.
2. `test_research_lab_tools…byte_identical_to_pre_fix_baseline`: 0.3366 vs 0.3465; the result depends on data and the data has changed.
3. `test_universe_selection`: 119 ≠ 120.
4. `test_instruments.ProvenanceMarkerTest`: the MT5 marker reads `yahoo_finance`.
5. `test_forex_terminal_data…currency_balanced`: 19 > 1.

**No failure is labelled pre-existing because it "looks unrelated".** Each one fails with the same name and the same error on clean `HEAD`.

### 4.4 Post-fix re-run
Code changed after the comparison runs: R5, journal mode, and the dashboard separation. A fresh full guarded run of the working tree started at 17:21. Targeted re-runs of every module touching the later dashboard edits follow it.

**Result: 0 regressions against clean HEAD.**

| | Post-fix working tree | Clean HEAD |
|---|---|---|
| Tests run | 1,592 (+54: Phase 1, journal-mode, dashboard, correlation, R5) | 1,538 |
| Fail + error | 86 | 85 |
| Network blocks | api.binance.com 2,089 · api.kraken.com 29 · data.binance.vision 12 · yahoo 2 | same hosts |

- **Failing on HEAD but passing now:** none.
- **Failing now but not on HEAD:** 1, `test_forex_terminal_data.ForexSystemHealthTest.test_never_calls_the_networked_provider_connectivity_check`.
  - **Cause, verified:** a run artifact. This shard imported `forex_terminal_data.py` before it was edited mid-run (the §6 dashboard separation). `inspect.getsource()` re-reads the file from disk, so the stale line numbers made the test parse the neighbouring, edited function. Its imports were `{pnl_by_execution_mode, TradeRecord}` instead of the intended function's.
  - **Re-run on the final code:** the test **passes**. The module's only failure is the pre-existing `test_universe_is_currency_balanced_not_usd_dominated`, identical on HEAD.
- **Targeted re-runs** of every module touching the dashboard and journal edits (74 tests: safety, data-layer contracts, Execution Monitor, forex execution, fixed-fraction, feature correlation): **all OK**.

---

## 5. A2: strategy-equity decision note

### 5.1 Before Phase 1
The heartbeat printed `compute_mark_to_market_equity` (the sum of the isolated margin accounts), and **nothing enforced it**. Collateral is refunded to spot after every close, so that sum reads ≈ 0 when flat. It was the wrong scope for a guard documented as covering *"this pilot's own sub-portfolio"*.

### 5.2 Current formula (implemented, tested)

    E      = C
           + Σ realised net P&L of closed pilot legs           [this engine's execution mode only]
           + Σ open legs [ d·q·(m − p_entry) − fee_entry
                           − accrued_interest·m  (LIVE short legs; once per isolated account) ]
    peak   = max(C, C + running realised P&L at each pair-trade completion, E)
    blocked ⇔ (peak − E)/peak ≥ 15 %   or   E ≤ 0   or   E cannot be established

Where:
- **C** = $500, the pilot-wide strategy capital that fixed-fraction sizing applies to;
- **d** = +1 for a long leg, −1 for a short;
- **m** = ticker last (see the open decision below);
- events are ordered by `closed_at`, falling back to `updated_at` only for rows closed before 0014.

### 5.3 Why it is mathematically correct
- **Same cash basis.** Realised `net_pnl` (canonical `compute_leg_pnl`, unchanged) already deducts entry and exit fees and borrow interest. The open-leg mark now deducts the entry fee *already paid*, and in live mode the interest *already owed*.
  - Before this fix, equity was overstated while a leg was open and then jumped down at the close: a discontinuity that is pure accounting artifact.
- **Peaks only at observable points.** A pair trade's realised result counts toward the peak only once both legs have closed. A journal-rebuilt peak is a **lower bound** on the true mark-to-market peak, so the guard can trip later than a continuously marked guard would, never earlier.
- **Fail closed** on: unvalued realised P&L, an unfetchable price, UNKNOWN-mode rows, an engine without a definite mode, missing capital, or E ≤ 0.

### 5.4 Why it is appropriate for trading
- The guard limits losses *of this strategy's capital*, not of the exchange account.
- Rebuilding from the journal makes it restart-proof: a restart cannot re-seed the peak lower.
- Exits are never blocked.

### 5.5 Mark-price convention: OWNER DECISION TAKEN (2026-10-02): **Option B, liquidation value**. Implemented and tested.

| Option | Open-leg value | Effect |
|---|---|---|
| **A** (current) | ticker **last**, no exit cost | Mark-to-market value. Can sit inside the spread or be stale. |
| **B** (**recommended**, strictest) | **liquidation value**: long at **bid**, short at **ask**, minus the estimated exit fee (venue fee × q × m); fail closed if bid or ask is missing | What the strategy would actually receive if forced to flatten, which is what a loss limit protects. Equity is lower by about (half-spread + fee) × open notional. At $25 legs that is roughly $0.05–0.10, negligible against the $90 cushion at 15 %. |

- **Edge cases under B:** missing bid/ask → block (not a silent fallback to last); crossed or zero book → block; dry run uses the same snapshot as the A4 fill model.
- **Tests required under B:** known-answer bid/ask marks for a long and a short; exit-fee deduction; missing-touch fail-closed; boundary (exactly 15 %) recomputed with liquidation marks.

**Why this was left to the owner:** the guard is the owner's risk limit, and how it is measured is risk policy, not a software fact.

**Implemented (Option B):**
- `BinanceIsolatedMarginAdapter.get_bid_ask()` reads bid and ask from one ticker snapshot, and raises on a missing, non-positive or crossed book (never a fallback to last).
- `strategy_equity` marks a long at the bid and a short at the ask, minus `FEE_RATE × q × mark`. Interest owed on live shorts is valued at the same ask.

**Tests** (`LiquidationMarkTests`, hand arithmetic):

| Case | Result |
|---|---|
| Long at bid | 500 − 1.00 − 0.099 = **498.901** |
| Short at ask | 500 − 0.20 − 0.0202 = **499.7798** |
| Spread alone at the boundary | 600 peak, open long 1,000 @ 1.0, bid 0.91 → 509.09 → **blocks**. Under the old last-price mark this read 600 and was allowed. |
| Missing / zero / crossed book | Each raises, and the guard fails closed |
| No open legs | No quote needed |

- Earlier expectations were updated by exactly the hand-computed exit fee (0.1; 0.004).
- **Mutation check:** mid instead of bid/ask → 3 fail; sides swapped → 3 fail; exit fee removed → 5 fail.

### 5.6 Remaining A2 limitations (documented, not hidden)
- **Dry run:** borrow interest is unknown, so it isn't deducted. The guard is a simulation of a simulation and is labelled that way.
- **Third-asset fees:** a third-asset entry fee (e.g. BNB) is stored only in its valuable part. An open leg can therefore slightly overstate equity until its close makes `net_pnl` None, and at that point the guard fails closed.

---

## 6. Journal-mode decision (implemented)

**Evidence first.** The production journal holds 2 rows, both pairs dry-run (`dryrun-` order IDs, 2026-09-28). The legacy classification is therefore **unambiguous**; nothing has to be guessed.

| Design element | Choice and reason |
|---|---|
| `TradeRecord.execution_mode`, `ExecutionIncident.execution_mode` | `LIVE / DRY_RUN / RESEARCH / UNKNOWN`, indexed. **Set by the writer from its engine's `dry_run` flag**, never inferred at read time. A logger nobody configured writes `UNKNOWN`. |
| Backfill (0014 RunPython) | `order_id` beginning `dryrun` (pairs `dryrun-…`, main-bot `DRYRUN-…`) → `DRY_RUN`; **everything else stays `UNKNOWN`**. A row is never *assumed* live. |
| Provenance on `TradeRecord` | `methodology_version`, `protocol_hash` (the runner sets these from the same source `SignalRejection` uses); `data_version` (per-session seed; not yet threaded through: open item). |
| `closed_at` | A durable exit time. `updated_at` is `auto_now` and moves on any later save, so it cannot order a P&L path. |
| Drawdown guard | Counts only its own mode; any UNKNOWN pilot row fails closed. |
| Dashboards | Execution Monitor, the Crypto dashboard tile and the Forex tile show **LIVE-only** realised P&L, with other modes listed separately ("simulated" / "unattributed"). An unvalued row is counted, no longer added as 0. Pairs Pilot shows **one mode only** (LIVE if any live row exists, else DRY_RUN) and states it, with other-mode rows counted and not blended. |

**Prevents:** paper trades shown as live; dry-run P&L read as account P&L; mixed statistics; simulation in live monitoring; a live guard seeded by soak P&L. The last item was risk 7 of the Phase 1 report, now closed.

**Research datasets:** research engines write CSVs, not `TradeRecord`, so live rows cannot contaminate them today. `RESEARCH` is reserved for any future research writer.

**Still open:**
- `data_version` per row;
- `OPEN`-leg startup reconciliation still counts dry-run OPEN legs when a *live* engine starts. This is conservative (it disables the pair) but should become mode-aware before a live switch;
- `terminal_data`'s recent-trades tables list all modes without a mode column.

---

## 7. Migration status

| Migration | Changes | Why | Backward compatible? |
|---|---|---|---|
| **0012** | `TradeRecord.reconciliation_affected` (bool, default false), `reconciliation_gaps` (text, default ''); new table `bot_signalrejection` (+ unique constraint per pair / venue / decision / reason) | Stage 2D D-1/D-2 provenance | Yes: additive; existing rows read as "clean exit" |
| **0013** | New table `bot_executionincident`; `TradeRecord.borrow_cost_status`, `fill_model` (varchar, default '') | A1/A3 incidents; A4 labels | Yes: additive; old rows read as unlabelled ('') |
| **0014** | `TradeRecord.execution_mode` (default 'UNKNOWN', indexed), `methodology_version`, `protocol_hash`, `data_version` (default ''), `closed_at` (nullable); `ExecutionIncident.execution_mode`; RunPython backfill | Journal-mode separation, provenance, durable exit time | Yes: additive; reverse = no-op data step, then the columns drop |

- **Verified:**
  - `makemigrations --check` reports no drift;
  - `sqlmigrate` shows only `ADD COLUMN … DEFAULT … / DROP DEFAULT`, `CREATE TABLE` and `CREATE INDEX`: no destructive SQL;
  - every test database applied 0001→0014 cleanly.
- **Observed state.** The local database `quantbot_pro@127.0.0.1` shows 0012, 0013 and 0014 applied at **2026-10-02 17:16:03 (local)**, about a minute after 0014 was generated and 15 s before `runserver` restarted. **This audit never ran `migrate`.** Nothing in the code auto-migrates the local database. The pattern matches an operator running the §9 instruction. The 0014 backfill ran: the 2 rows are now `DRY_RUN`.
- **Consequence:** 0014 must not be edited again. Any further schema change needs 0015.
- **Command** (for any other environment): `python manage.py migrate`
- **Railway (owner decision 2026-10-02: manual migrations, implemented):**
  - `railway.toml` previously ran `migrate --noinput` on every deploy.
  - It now runs `python manage.py migrate --check`, which **verifies and never applies**: it exits non-zero on any unapplied migration, so a deploy refuses to start on an outdated schema. That's the same rule the pairs runner enforces.
  - To migrate production, the operator runs `python manage.py migrate` against Railway's `DATABASE_URL` (e.g. `railway run python manage.py migrate`), then redeploys.
  - **Note:** the Railway database has not had 0012–0014 applied by this audit. The next deploy will refuse to start until the operator migrates it.

---

## 8. Phase 1 verdict

**Pairs live safety: READY: for continued DRY_RUN soak only.**

Conditions (all met):
1. ~~The owner decides the A2 mark convention.~~ **Met:** liquidation value, implemented and tested (§5.5).
2. ~~The post-fix full re-run shows no new failures against `HEAD`.~~ **Met** (§4.4: 0 regressions).
3. ~~The Railway auto-migrate decision.~~ **Met:** verify-only (§7).

**Live capital: BLOCKED**, independently of Phase 1:
- no canonical eligibility producer (every pair is blocked for new live entries by design);
- Stage 2D Steps 12–20 not run;
- the Kalman pairs "edge" was found to be a P&L-measurement artifact in the 09-29 audit. **The strategy is a research hypothesis, not an established edge.**

A software-safe execution layer does not make the strategy worth trading.

---

## 9. Phase 2: DCA B1–B8: Implemented · Tested · Verified (research-only)

The specification was written **before** implementation: `DCA_RESEARCH_PROTOCOL_2026-10-02.md` (canonical clock, fill models, accounting, nulls, leakage rules).

| # | Finding (verified against code first) | Remediation | Evidence |
|---|---|---|---|
| B1 | One level per candle, filled at the crashed close; called "conservative" | Explicit fill models. **NEXT_OPEN** (default): close-confirmed, one level per decision. **INTRABAR_LADDER**: resting limits at known triggers, filled from the low/high, at the open on a gap, several levels per candle; deterministic from OHLC. **LEGACY_CLOSE**: old clock, labelled. The "conservative" claim was removed. | Crash candle (low 60): INTRABAR fills 4 levels at 95 / 88.35 / 79.515 / 67.58775 (hand arithmetic); NEXT_OPEN fills 1 |
| B2 | Same-candle decide and fill; new cycle on the exit candle | Decide at close(c), execute at open(c+1); exit → next entry decided at close(c+1) | No-look-ahead test: changing every candle after k leaves every close before k byte-identical |
| B3 | Realised and censored mixed; no unrealised view | Per-candle MTM frame; `summarize_dca_tail_risk` (max capital, depth, unrealised loss, MTM drawdown, time in cycle, longest cycle, longest underwater, unrecovered flag, censored cycles); realised / censored / MTM reported separately. `total_net_profit` keeps its old meaning (= MTM) so earlier numbers stay comparable. | Known answer: −1.2 = 0.06 × (80 − 100) |
| B4 | Null preserves drift exactly; B = 100 | OHLC-aware nulls: **BLOCK_SHUFFLE** (drift-preserving) and **DRIFT_REMOVED_BLOCK_SHUFFLE**; MTM statistics; B default 999; B < 999 → `INSUFFICIENT_RESOLUTION`; `run_dca_null_suite` across block sizes (24/168/720 h) with in-suite Bonferroni and a reading table (drift vs path structure) | Endpoint preserved / removed exactly; candle shapes are a permutation of the real ones |
| B5 | FX volatility scale from full history | Full-history scaling refused; a calibration window is required; `make_fx_scaled_oos_fns` fits per fold on the purged training slice | Positive control: fitted = prefix-only to 1e-12; a leak would move it ×2.44 (analytic) |
| B6 | Free shorting | `financing_status` NOT_APPLICABLE / KNOWN / UNAVAILABLE / ASSUMED_ZERO; KNOWN charged per elapsed hour on the cost basis; SHORT and FX default to UNAVAILABLE → `cost_complete=False` | Known answer: 4 h × 0.001 × 6 = 0.024 |
| B7 | Exhaustion vs observed max (feature experiments; `summarize_dca_cycles` was already fixed) | Configured level count, derived from the events and checked | A bucket maxing at level 3 of 6 → 0 %, not 66.7 % |
| B8 | Gaps compressed; timeout in rows | Modal Δ; `gap_count`, `gap_hours`, `holding_hours`; elapsed-time timeout; no invented prices | 10 h gap → 9 gap hours; timeout after 3 rows because 12 h elapsed |

**Tests:**
- 32 new (`test_dca_phase2`) + 27 legacy (pinned to `LEGACY_CLOSE`; expected values **unchanged**, which proves the old clock reproduces exactly).
- **Mutation check:** each of the 8 fixes reverted alone → its test fails (8/8).
- **Own test errors caught and corrected:**
  1. a NEXT_OPEN expectation I had wrong ([0, 1], not [0, 1, 2]);
  2. a B5 positive control too weak to catch reference-side leakage (strengthened);
  3. a "bracket" claim in my own protocol, contradicted by real data (corrected, see below).

**Research-integrity review (descriptive only, in-sample, one asset, no significance claim):** baseline ladder on local BTC/USDT 1 h, 2021-09-26 → 2026-09-24 (43,797 candles), no network.

| | LEGACY_CLOSE | NEXT_OPEN | INTRABAR_LADDER |
|---|---|---|---|
| Cycles · win rate (resolved) | 190 · 99.5 % | 191 · 99.0 % | 226 · 98.7 % |
| Realised P&L · censored mark (USDT) | +159.70 · −0.16 | +170.45 · −0.17 | +175.96 · −0.17 |
| Max unrealised loss | −313.53 | −298.57 | −118.93 |
| Max MTM drawdown (% of $762 ladder) | 41.2 % | 39.2 % | 16.0 % |
| Longest underwater | 8,385 h | 7,846 h | 3,021 h |
| Recovered by data end | no | no | no |

Reading:
- A ~99 % cycle win rate coexists with an unrealised loss up to **1.9× the entire five-year realised profit** and roughly a year underwater. **Win rate is not edge** (master prompt §18).
- The fill model changes the drawdown by about 2.5×, so a DCA conclusion that holds under one clock only is not robust.
- **The P&L ordering of the two canonical models is empirical:** INTRABAR showed the *smaller* drawdown here. My protocol's first draft said the models "bracket exposure", which real data contradicted; it was corrected to "bracket deployment speed".

**Prior DCA conclusions are void as evidence.** Any earlier DCA result (e.g. per-asset "closest to significant" readings) used the same-print clock, drift-preserving B = 100 nulls and, for FX, full-history volatility scaling. It is a **research hypothesis at most** until re-run under this protocol.

**Not run (needs an explicit research decision):** the full null suite. 2 nulls × 3 block sizes × 999 permutations ≈ 6,000 simulations ≈ **4.5 h per symbol per fill model** at 2.7 s per simulation, and its output would be a research conclusion requiring a pre-declared family. `dca_engine.py` remains research-only: not wired to live trading or the dashboard.

## 10. Phase 3: main bot / backtester: **Not started**
H-07 (forming candle), M-01, M-02 and H-11 are open. Note: both main strategies are currently gated off (MovingAverage UNTESTED, MeanReversion REJECTED), so backtests produce 0 trades by design.

## 11. Phase 4: research statistics: **Plan only**
H-12, M-03 and M-04, plus the legacy permutation (C-03): see Phase 1 report §18. No methodology changed.

## 12. Phase 5: backfill / data integrity: **Not started**
- Detection, reconciliation state and entry blocking are verified.
- Backfill is not implemented.
- Five data / environment test failures (§4.3) are real data-integrity items, including candles that are **7.6 days stale**.

## 13. Feature correlation

| Item | Status |
|---|---|
| Page upgrade | Implemented · Tested (11 tests) · **Verified** (4/4 mutations caught) |
| Rebuilt observation files | **Staged, not swapped** (Phase 6, per order) |
| Validator labels | **Not re-run** (H-12 open) |

Page upgrade details:
- stationary basis by default (price levels ÷ close − 1, dollar amounts ÷ close), raw still selectable;
- Spearman option;
- empty and constant columns and exact duplicates reported separately;
- redundancy pairs labelled BY CONSTRUCTION / EMPIRICAL with a per-year stability range;
- a cache keyed on file version;
- a reading guide.

Rebuilt observation files:
- 20 files from local `data/` with the current `FeatureCalculator` (steps 1–3 only), no network;
- ADX is populated (BTC 43,771 / 43,797 rows; the 26 missing are warm-up);
- `atr_indicator` is confirmed identical to `atr`;
- MATIC data ends 2024-09 (rebranded to POL).

The July `SUPPORTED / REJECTED` labels are kept as they were.

## 14. Stage 2D status
**Steps 12–20 BLOCKED.** Zones S / V / WF / F / L_H, the holdout and the FX calendar are blocked by the owner. Readiness is to be re-assessed in Phase 7, after the research-integrity work.

---

## 15. Five-area rating (Phase 1 scope)

| | Software | Mathematics | Statistics | Quant trading | Governance |
|---|---|---|---|---|---|
| A1 naked leg | Verified | n/a | n/a | Economically justified (fail-closed unwind) | Verified (durable incidents) |
| A2 guard | Verified | Verified (cash basis, liquidation marks, peak points, boundary) | n/a | Economically justified (liquidation value, owner-chosen) | Verified (mode-separated) |
| A3 exit minimums | Verified | Verified (boundary) | n/a | Economically justified | — |
| A4 dry-run fills | Verified | Verified (known-answer) | n/a | Improved, still **no depth / no borrow** (labelled) | Verified (labels) |
| Journal mode | Verified | n/a | n/a | Economically justified | Verified (backfill never assumes live) |
| Migrations | Verified (`sqlmigrate`, no drift) | n/a | n/a | n/a | Verified (Railway verify-only; operator migrates) |
| Feature correlation | Verified | Verified (units, transforms) | Tested (Spearman robustness, per-year stability) | Research tool only | Data swap pending; labels untouched |

**Statistics:** Phase 1 makes **no** statistical claim.

## 16. Explicit risks
1. ~~A2 mark convention undecided.~~ Decided: liquidation value.
2. An ambiguous order may fill *after* the unwind re-read; nothing watches for that late fill.
3. Unwind market orders can fill at poor prices (recorded truthfully).
4. A NO_EXPOSURE rejection can repeat at every signal (never compounds exposure).
5. Dry run has no depth model and no borrow cost (labelled).
6. Backfill not implemented.
7. ~~Railway auto-migrates on deploy.~~ Now verify-only. **The next Railway deploy will refuse to start until the operator migrates the Railway database.**
8. Startup OPEN-leg reconciliation is not mode-aware.
9. Five data / environment test failures, including stale candles.
10. An exposure discovered only by the unwind has no entry fill, so its P&L is not journaled (it is in the incident).

## 17. Explicit assumptions
- **Dust:** base below minQty (or step) counts as flat.
- **Strategy capital:** C = $500, pilot-wide.
- **Guard boundary:** "≥ 15 % blocks" is a float comparison; exact for rational cases.
- **Dry-run fills:** full fill at touch × (1 ± declared slippage).
- **Ordering:** by `closed_at`, falling back to `updated_at` for legacy rows.
- **Journal mode:** set by the writer from its engine flag. A row of unknown origin is UNKNOWN, never LIVE.

## 18. Evidence index
- **Regression:** `regression_matrix.tsv`; shard logs and per-host network-block logs (scratchpad, this session).
- **Mutation checks:**
  - A1–A4: guard 9/27 fail, minNotional 3/27 fail;
  - R1–R4: 2/2/1/1 fail;
  - journal mode / A2: 6/6 caught;
  - feature correlation: 4/4 caught.
- **Arithmetic** (exact fractions):
  - ask 100 / bid 99 → buy fill 100.05, sell fill 98.9505;
  - boundary 90/600 = 3/20.
- **Migrations:** `sqlmigrate 0012/0013/0014`; `django_migrations` timestamps; row modes `DRY_RUN: 2`.

## 19. Recommended next sequence
1. ~~Owner decides A2 and Railway auto-migrate.~~ Done; both implemented.
2. ~~Confirm the post-fix re-run.~~ Done: 0 regressions.
3. ~~Phase 2: DCA B1–B8.~~ Done (§9). Optional, needs a decision: a pre-declared DCA null-suite research run.
4. Phase 3: H-07, M-01, M-02, H-11.
5. Phase 4: M-03 → M-04 → H-12, plus a C-03 review.
6. Phase 5: backfill architecture and the stale-data items.
7. Phase 6: feature-data swap, with fingerprints.
8. Phase 7: re-assess Stage 2D.

## 20. Final readiness classification
- **Phase 1 pairs live safety:** **READY for DRY_RUN soak only** (all §8 conditions met).
- **Live trading:** **BLOCKED.**
- **Pairs strategy edge:** **research hypothesis** (09-29 finding: the P&L was a measurement artifact).
- **DCA engine:** remediated (B1–B8). **DCA strategy edge:** research hypothesis; no valid significance evidence exists yet. The descriptive BTC run shows severe trapped-capital tail risk behind a ~99 % win rate.
- **Repository:** **not fully audited** (dashboard views, research lab, academy, Kraken layer, cross-sectional/PCA/contagion engines, `data_fetcher` and main strategies remain unaudited).
