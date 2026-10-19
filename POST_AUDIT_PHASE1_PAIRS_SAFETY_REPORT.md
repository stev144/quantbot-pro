# Post-Audit Phase 1 — Pairs Live Safety Report

**Date:** 2026-10-02. **Base:** branch `stage2d-revb` @ `565e55d` (Phase 1 changes are uncommitted).

**Scope:** Track A only (A1–A6). Stage 2D Steps 12–20, DCA (Track B) and the research statistics (Track C) were **not** touched.

## 0. Phase 0 — scope confirmation

**Read before starting:**
- `STEPH_QUANT_AUDIT_STATUS_2026-10-02.md` (the status audit);
- `QUANT_AUDIT_REPORT_2026-09-29.md` (C-04, C-05, M-12, L-03 entries);
- `STAGE2D_CANONICAL_PROTOCOL.md`, `STAGE2D_DECISION_LOCK.md`, `STAGE2D_PRE_STEP12_AUDIT.md`, `STAGE2D_D1_D5_G1_IMPLEMENTATION_REPORT.md`.

**Not claimed:** the 2026-10-02 audit did **not** cover dashboard views, the research lab, the academy, the Kraken/multi-venue layer, the cross-sectional/PCA/contagion engines, `data_fetcher`, or the main bot's strategies. Nothing here changes that: **the repository is not fully audited.**

---

## 1. Findings addressed

### A1 — C-04 naked leg / second-leg failure (HIGH) — FIXED

**Trace, as found** (signal → sizing → leg A → leg B):
- Only `OrderStillOpen`, `OrderUnconfirmed` and `RateLimit` were caught. Any other exception after leg A filled propagated to the runner's generic handler, leaving:
  - leg A **naked**;
  - the pair **enabled**;
  - **nothing journaled** (legs were logged only after both filled);
  - no tracked position;
  - a phantom trade in the signal engine.
- Even in the caught branch:
  - the unwind result was never confirmed;
  - collateral was refunded **even if a short might still be open** (a liquidation risk);
  - the pair was disabled in memory only, so **a restart re-enabled it**.
- `close_pair_trade` had the same 3-type gap. A failure on leg B after leg A closed desynchronised the journal and the tracked position.

**Fix (`bot/pairs/execution.py`):**

1. **Every** exception raised after the first order is submitted goes to `_handle_open_failure`:
   1. `_attempt_partial_unwind` closes whatever the isolated accounts **actually** hold, on both symbols. That also covers a leg the exchange filled although the call raised.
   2. It **re-reads both accounts** and returns `(confirmed_flat, fills, exposure_found)`. "Flat" means no debt and base below the dust limit (minQty, or step).
   3. Every leg **known** to have filled is journaled. A confirmed unwind gets its real exit (`exit_reason = PAIR_OPEN_UNWOUND`, real P&L). An unconfirmed leg stays **OPEN** in the journal, which startup reconciliation treats as exposure.
   4. The failure state is explicit (an `ExecutionIncident` row; see §6):
      - `NO_EXPOSURE`: nothing filled, both accounts verified flat. The pair stays tradeable.
      - `UNWOUND_CONFIRMED`: a leg filled and was unwound, verified flat. The pair is disabled for this run.
      - `MANUAL_REVIEW_REQUIRED`: the unwind was not confirmed, **or** the failing order's status is ambiguous (`OrderStillOpen` / `OrderUnconfirmed`, which may still fill after we looked). The pair is disabled **across restarts**.
   5. Collateral is refunded **only when confirmed flat**.
2. **No retries.** A failed entry is never re-attempted by the execution layer. The adapter's existing bounded rate-limit retry happens *before* an order exists, so it cannot compound exposure.
3. **Restart:** `pairs_bot_runner.reconcile_pairs` now also disables every pair with an unresolved `MANUAL_REVIEW_REQUIRED` incident, in addition to the existing OPEN-leg check.
4. **Close path:** legs are closed and journaled **one at a time**. Any failure (not just 3 types):
   - leaves that leg OPEN in the journal **and** in the tracked position (`leg_a_open` / `leg_b_open`);
   - disables the pair;
   - records `CLOSE_LEG_FAILURE`, or `CLOSE_BLOCKED_EXCHANGE_RULES` (§A3), as MANUAL_REVIEW_REQUIRED;
   - never fabricates a fill, and leaves collateral in place.

   A later close attempt closes only the remaining leg(s).

### A2 — C-05 drawdown guard (HIGH) — FIXED, with one definition for owner confirmation

**Documented semantics, unchanged:**
- `DrawdownGuard`: threshold `MAX_DRAWDOWN_PCT = 0.15` (`bot/config/risk.py`);
- peak watermark;
- **blocked when drawdown ≥ threshold** (the boundary blocks);
- auto-clears on recovery;
- entries only: exits are never blocked.

**Defect found in the equity input.** The pairs engine's own documentation scopes the guard to *"this pilot's own sub-portfolio"*, but fed it `compute_mark_to_market_equity`: the sum of the isolated margin accounts. Collateral is transferred back to spot after **every** close, so that sum reads ≈ 0 when flat. Enforcing it would have blocked all trading after the first trade, or never tripped at all. It was also never consulted before an entry.

**Fix:** a strategy-level equity matching the documented scope:

    equity = C + Σ realised net P&L of closed pilot legs (journal)
               + Σ unrealised mark P&L of open pilot legs (current ticker)
    peak   = max(C, C + running realised P&L after each closed leg, current equity)

- C is the strategy capital (`pair_config.capital_usdt`, $500).
- The peak is **rebuilt from the journal on every check**, so a restart can't re-seed it lower. This closes the restart gap documented in `drawdown_guard.py` itself.
- **Fail closed:** a closed leg with unvalued P&L (a third-asset fee) or an unfetchable price blocks the entry.
- **Enforced inside `open_pair_trade`**, before any transfer or order, on every entry. Simultaneous signals are re-checked one by one, each including the legs just opened (the runner is single-threaded).
- The heartbeat logs the same check. The old isolated-account function remains as a diagnostic only, marked as not the guard input.

**For owner confirmation:**
- Open legs are marked **without** their (unknown) borrow interest and exit costs, so equity is slightly optimistic for open shorts.
- The threshold was **not** changed.

### A3 — Exit minimum order / size (NEW finding) — FIXED

- `_require_closable(symbol, qty)` raises `CloseQuantityUnavailableException` **before any order** when qty × current mark < minNotional. It is called for long closes and for short cover buys (a cover buy is never "grossed up" to reach the minimum).
- minQty and step were already enforced by `sellable_quantity` / `cover_buy_quantity`.
- In dry run the simulator has no exchange filters (`min_notional = None`), and nothing is claimed.
- A blocked leg follows the A1 close path: it stays OPEN with its real remaining position, the pair is disabled, and a `CLOSE_BLOCKED_EXCHANGE_RULES` incident is recorded.
- `accounting.exit_notional_ok` remains unused. The check lives at the execution boundary, where the exchange metadata is.

### A4 — M-12 dry-run fill realism — FIXED

**Fill model, documented** (`margin_adapter._simulate_fill`, `get_touch_price`):

| Element | Before | Now |
|---|---|---|
| Decision timestamp | cutoff t (signal engine) | unchanged |
| Fill timestamp | runner processing time after close(c) | unchanged (live latency; not the candle close) |
| Reference price | ticker **last** | side-correct **touch** from one ticker snapshot: **ask** for a buy, **bid** for a sell; last only as a labelled fallback |
| Slippage | declared `SLIPPAGE_RATE` on last | declared `SLIPPAGE_RATE` beyond the touch (market impact); the spread is now paid explicitly |
| Fee | `FEE_RATE` on notional, USDT | unchanged |
| Partial fills | full fill, unlabelled | full fill, **labelled** |
| Borrow | interest 0, silently | **`borrow_cost_status = UNAVAILABLE`** on dry-run short legs (`KNOWN` in real mode, `NOT_APPLICABLE` for longs); dry-run interest is passed as unknown, never as a real zero |

**Labels:** every journal leg carries `fill_model`, for example `DRY_RUN_TOUCH+SLIPPAGE|exit:DRY_RUN_TOUCH+SLIPPAGE`.

**P&L:** `compute_leg_pnl` and `bot/pairs/pnl.py` are unchanged. With interest unknown, the existing formula infers the same number as before (0 in the simulator) and now flags it as inferred.

No parameter was tuned. The declared slippage and fee constants are unchanged.

### A5 — Migration readiness — ADDRESSED (not applied)

- Phase 1 adds migration **`0013`** (`ExecutionIncident`, `TradeRecord.fill_model`, `TradeRecord.borrow_cost_status`) alongside **`0012`** (D-1/D-2).
- **New:** `pairs_bot_runner.run_bot()` checks `pending_migrations()` at startup and **refuses to start** with a CRITICAL log naming the migrations and the command. An unverifiable schema also refuses.
- Without this, a missing column would make every journal write fail (logged CRITICAL, but the position record would be lost).
- **Migrations were not applied.**

### A6 — Missed-candle reconciliation — VERIFIED (no code change in Phase 1)

| Requirement | Covered by |
|---|---|
| Detection | `test_canonical_seed.LiveCandleSequenceTests` |
| Reconciliation state entered | `test_canonical_seed.LiveCandleSequenceTests` |
| New entries blocked | `test_canonical_lifecycle.test_reconciliation_blocks_entries_until_confirmed` |
| Exits allowed, marked and persisted through the runner | `test_stage2d_provenance.RunnerProvenancePathTests.test_eligible_entry_then_reconciliation_affected_exit_is_passed_through`; `ReconciliationProvenancePersistenceTests` |
| Clean parity excludes affected trades | `ParityConsumerDistinguishesTests` |

**Backfill remains an explicit open item.**

### A7 — Adversarial review of the Phase 1 code itself — 4 defects found and FIXED

The first Phase 1 pass was re-read line by line against the five correctness axes (§19). Four defects were found **in the new code**. Each is now fixed in `bot/pairs/execution.py`, with a regression test that fails when that one fix is reverted (mutation check, §4).

| # | Axis | Defect | Fix |
|---|---|---|---|
| R1 | Software / risk (fail-open) | `DrawdownGuard.update()` returns its **previous** state when the balance is ≤ 0. That behaviour was meant for a failed fetch that returns 0.0. On a fresh engine the previous state is "not tripped", so a pilot whose strategy equity had fallen to ≤ 0 (all capital lost) would have been **allowed** to enter. | `drawdown_entry_check` blocks explicitly when equity ≤ 0. `drawdown_guard.py` itself is unchanged, so its other callers are unaffected. |
| R2 | Mathematics | The peak was updated after every closed **leg**. If leg A's +100 closed before leg B's −100, the peak became C + 100, a level the pilot never held, giving a false 16.7 % drawdown on a flat pair. | Peak points occur only when a pair trade's **last** leg closes. A half-closed pair (one leg still OPEN) is never a peak point. Pre-0010 rows with no `pair_trade_id` each count as their own event. |
| R3 | Quant / execution | In `open_short`, if the borrow succeeds and the sell then fails, the account holds free base equal to the debt. The unwind **market-sold** that borrowed base, re-opening the very short being unwound and repeating the order that had just failed, then needed a cover buy. If that sell failed again, a position a single repay would have flattened went to MANUAL_REVIEW. | Free base is **repaid** against the debt first. Market orders handle only what remains. |
| R4 | Software / state | A leg that reported a fill but had no unwind fill (the account read flat, e.g. exchange lag) stayed journaled OPEN, while the incident said `UNWOUND_CONFIRMED`. | Any filled leg left OPEN in the journal forces `MANUAL_REVIEW_REQUIRED`. |

---

## 2. Files changed

| File | Change |
|---|---|
| `bot/pairs/execution.py` | A7 review fixes R1–R4; A1 open/close failure handling, confirmed unwind, per-leg close, `OpenPairPosition.leg_*_open`; A2 `strategy_equity`, `drawdown_entry_check` (enforced in `open_pair_trade`); A3 `_require_closable`; A4 borrow-status labels |
| `bot/pairs/margin_adapter.py` | A4 `get_touch_price`; `_simulate_fill` touch + slippage; `MarginFillResult.price_source` |
| `bot/pairs/trade_logger.py` | `fill_model` / `borrow_cost_status` journaling; `log_incident`; `unresolved_manual_review_pairs` |
| `bot/core/pairs_bot_runner.py` | A5 `pending_migrations` + startup refusal; A1 incident-based disable in `reconcile_pairs`; A2 heartbeat on the same guard check |
| `bot/journal/models.py` + `bot/migrations/0013_…` | `ExecutionIncident`; `TradeRecord.fill_model`, `borrow_cost_status` |
| `bot/tests/test_pairs_live_safety.py` | new, 33 tests (27 first pass + 6 A7 review regressions) |

## 3. Files not changed

- `bot/pairs/pnl.py`: 0-line diff; no independent need found.
- `bot/research/dca_engine.py`: 0-line diff (Track B not started).
- `bot/pairs/signal_engine.py` and every Stage 2D module.
- `bot/risk/drawdown_guard.py`: semantics reused as is.
- Configuration and risk constants.
- Research engines.

## 4. Tests added (`bot/tests/test_pairs_live_safety.py`, 33)

An in-memory isolated-margin ledger with deterministic failure injection: an exception, optionally **after** the exchange executed the order. No network.

| Area | Tests |
|---|---|
| A1, the 12 required scenarios | 1 leg A fails pre-fill → NO_EXPOSURE, collateral refunded, pair tradeable · 2 both fill → 2 OPEN legs, no incident · 3+6 leg B rejects → leg A unwound with a real exit, flat, UNWOUND_CONFIRMED · 4+8 leg B times out **after executing** → both symbols unwound from exchange state, MANUAL_REVIEW (ambiguous) · 5 unknown exception (`KeyError`) handled · 7 unwind fails → leg A stays OPEN, collateral **not** moved, MANUAL_REVIEW · 9+10 restart: a fresh engine plus `reconcile_pairs` re-disables from the journal · 11+12 across all failure types: never a completed pair, and any residual exposure ⇒ MANUAL_REVIEW |
| A3 | valid close (long and short legs, borrow labels) · exactly minNotional closes · below minNotional: blocked leg gets no order and stays OPEN, the other leg closes, the remaining position is preserved, incident recorded, later completion closes only the remaining leg · short cover below minNotional · below minQty / tiny remainder · step rounding leaves dust |
| A2 | no history allows · threshold: 84.17 % equity of peak allows; **exactly 15 % drawdown blocks**; above blocks · unrealised open legs count · restart rebuilds the peak · unvalued P&L and price failure fail closed · a blocked entry reaches **no** transfer or order · existing positions still close while tripped · a second simultaneous signal is re-checked including the first pair's open legs |
| A4 | touch + slippage + fee arithmetic · last-price fallback labelled · dry-run journal: short `UNAVAILABLE`, long `NOT_APPLICABLE`, fill-model labels |
| A5 | schema current in tests · runner refuses to start with pending migrations (`load_pair_configs` never reached) |
| A7 (`Phase1ReviewRegressionTests`, 6) | R1: equity exactly 0 and −150 fail closed on a fresh guard · R2: a +100/−100 pair is a 0 % drawdown (allowed) · R2: a half-closed pair (+100 realised, −100 open mark) gives equity = peak = 500 · R2: pre-0010 rows without a pair id are separate peak points (600 → 510 blocks) · R3: borrowed-but-unsold base is repaid, with **no** market sell or cover on that symbol · R4: a filled leg without an unwind fill ⇒ MANUAL_REVIEW and the leg stays OPEN |

**Mutation check:**
- disabling the guard → **9 / 27 fail**;
- disabling the exit minNotional check → **3 / 27 fail**;
- A7, each fix reverted one at a time (scripted, file restored byte-identical afterwards): R1 → 2 fail · R2 → 2 fail · R3 → 1 fail · R4 → 1 fail, each time exactly the matching regression test(s).

The tests are not vacuous.

## 5. Focused test results

`test_pairs_live_safety`: **33 tests, OK** (run with every non-loopback connection refused; 0 connection attempts recorded).

During development, one test of my own had wrong arithmetic (it expected a 10 % loss to trip a 15 % guard). It was corrected to a −17.5 % case; the code was not changed for it.

## 6. Regression results

Full suite, both trees, network refused, matched by exact test name. Full matrix: `QUANT_BOT_POST_AUDIT_COMPLETION_REPORT_2026-10-02.md` §4.

| | Working tree | Clean HEAD |
|---|---|---|
| Ran | 1,571 | 1,538 (+33 new Phase 1 tests) |
| Fail + error | 86 | 85 |

- **1 Phase 1 regression** (`test_fixed_fraction_execution…test_open_pair_trade_refuses_before_any_transfer`), **fixed** (R5: missing capital fails closed; the test asserts the real minQty refusal).
- **85 failures are identical on clean HEAD:**
  - 58 are blocked network calls;
  - 22 are blocked calls surfacing indirectly;
  - 5 are data/environment items, listed as open.
- A post-fix full re-run is reported in the master report §4.4.

## 7. Quantitative assumptions (explicit)

- **Dust limit:** base below minQty (or step) is treated as flat. It cannot be sold by a market order.
- **Equity:** open legs are marked at the ticker **last** price, without borrow interest or exit costs (unknown until the close). Realised P&L comes from the journal's `net_pnl`.
- **Dry-run reference price:** the touch from a single ticker snapshot; full fill; the declared slippage is impact beyond the touch.
- **Drawdown:** 15 % unchanged, measured from the peak of the strategy-equity path.
- **Peak points** exist only at pair-trade completions (R2). Their order comes from the row's `updated_at`, because `TradeRecord` has no exit-timestamp field. A later manual edit of a closed row would move it in that order.
- **A journal-rebuilt peak is a lower bound** on the true mark-to-market peak: open-leg marks between journal events are not stored. The guard can therefore trip later than a continuously marked guard would, never earlier.
- **Boundary:** "≥ 15 % blocks" is a float comparison. Exact rational cases (90/600 = 3/20) evaluate exactly; a value within ~1e-16 of the threshold is not guaranteed either way.
- **Independent arithmetic check** (exact fractions, not the code under test): ask 100 / bid 99, s = 0.0005, fee 0.001 → buy fill 100.05, sell fill 98.9505, buy fee on 2 units 0.2001. The round trip costs 1.105 % of mid before fees (spread 1.005 % + about 2s), so the spread is now paid explicitly.

## 8. Remaining risks

1. **The A2 equity definition needs owner confirmation** (see §A2). It implements the documented sub-portfolio scope, but it is a new formula. Borrow interest is excluded from open-leg marks.
2. **Ambiguous orders after an unwind:** an `OrderUnconfirmed` order that fills *after* our unwind re-read would create new exposure. The pair is in MANUAL_REVIEW, but nothing watches for that late fill. That is a human or reconciler task.
3. **The unwind itself places market orders.** In a fast market the unwind price can be poor. It is recorded truthfully as a real exit.
4. **`NO_EXPOSURE` leaves the pair tradeable.** A persistent pre-fill rejection (e.g. insufficient funds) would be re-attempted at the next signal. Each attempt is verified flat, so exposure cannot compound, but the noise can.
5. **Dry run still has no depth model** (full fills) and no borrow cost. Both are now labelled, not modelled.
6. **Missed-candle backfill is not implemented.** Entries stay blocked after a live gap until restart.
7. **The journal does not separate dry-run from live rows.** `TradeRecord` has no `dry_run` field, and rows written before 0013 have an empty `fill_model`. The guard's strategy equity would therefore include dry-run soak P&L after go-live: dry-run gains would give live trading an unearned cushion, and dry-run losses would block it. **Before any live switch, the owner must decide how to partition the journal** (e.g. start a new strategy tag, or archive the dry-run rows). Not changed here: that is a governance decision, not a safety fix.
8. **An exposure found only by the unwind (the failing order raised but executed) has no entry fill**, so its unwind P&L is not journaled. It is recorded in the `ExecutionIncident` (MANUAL_REVIEW), but strategy equity does not include that cost until a human journals it.

## 9. Migration requirement

Before restarting **any** code that writes journal fields, run:

    python manage.py migrate

This applies **0012** (reconciliation provenance, SignalRejection) and **0013** (ExecutionIncident, fill/borrow labels). The pairs runner now refuses to start until this is done. **I did not apply migrations.**

## 10–15. Confirmations

10. **No exchange calls from the Phase 1 code or tests.** Every Phase 1 test uses an in-memory ledger or a stub ccxt object, and the final runs refused every non-loopback connection. **Disclosure:** one earlier full-suite attempt this session ran *without* the network guard. Under the repo's no-mocking convention its pre-existing tests made **read-only public** market-data requests (`load_markets` / ticker / klines; no credentials, no orders) for about 10 minutes, until I stopped it because it conflicted with this brief. Its results were discarded.
11. **No live trading;** the bot was not started.
12. **No 150-symbol scan.**
13. **No holdout** defined or evaluated.
14. **No Stage 2D Step 12** (or any of Steps 12–20).
15. **DCA was not modified** during Phase 1: `bot/research/dca_engine.py` has a 0-line diff.

## 16. Unresolved DCA findings (Track B — Phase 2, awaiting approval)

- B1 / H-08: at most one ladder level per candle, filled at the crashed price. The bias is optimistic, not "conservative".
- B2: same-candle decision and fill; a new cycle opens on the exit-fill candle.
- B3 / H-10: no intra-cycle unrealised drawdown; data-end mark-to-market mixed into realised profit.
- B4 / M-05: the permutation preserves start and end price (drift cannot be tested); B = 100.
- B5 / H-09: the FX volatility scale is fitted on full history (leakage).
- B6 / M-10: free shorting assumed (no borrow or swap; no BORROW_* status).
- B7: the exhaustion rate in the feature experiments uses each bucket's observed maximum level.
- B8: data gaps compressed; `candles_held` counts rows.
- M-06 / H-11: `r_multiple` is a return on deployed capital, used in an R-based Sharpe.
- L-01: ladder field naming.
- DCA outputs carry no provenance.

## 17. Unresolved Stage 2D and other findings

- **Stage 2D:**
  - Steps 12–20 not started (Step 12, borrow-unavailable state, is next once the tracks are done);
  - zones S / V / WF / F / L_H **BLOCKED** (owner);
  - no canonical eligibility producer, so every pair is blocked for new live entries;
  - FX calendar **BLOCKED**;
  - missed-candle backfill deferred;
  - the legacy walk-forward / permutation / research-lab engines are not yet migrated.
- **Track C (plan only, not implemented):** H-12 (permutation resolution vs the BH family), M-03 (the walk-forward boundary candle), M-04 (OOS p-values assume independence). See §18.
- **Main bot / backtester** (not in Phase 1 scope): H-07 (forming candle), M-01, M-02, H-11.
- **Other:** M-11 (health drift vs the legacy seed), L-02 (stale docstrings).

## 18. Track C — short remediation plan (no methodology changed)

1. **H-12 (feature_validator):**
   - Make B a function of the family: B + 1 ≥ m / (q · k) for a target of k discoveries. For example, m = 2,000 and q = 0.05 needs B ≥ 40,000 for a single discovery.
   - Alternatively, a two-stage design: an analytic screen, then a permutation on the survivors, correcting within the declared screened family.
   - Needs a written spec (family definition, B, null, seed) and a synthetic recovery test (one planted feature among 2,000 nulls).
2. **M-03 (walk_forward_engine):**
   - Use an exclusive train end (`test_start = train_end + 1 candle`).
   - Apply the derived purge (last d + H candles of train excluded) and label truncation from the decision lock.
   - Needs a fold-boundary unit test.
   - Small; can be done with the Stage 2D WF migration (Step 14).
3. **M-04 (oos_validator):**
   - Replace IID Spearman p-values with a block-bootstrap or HAC (Newey-West, lag ≥ the label horizon) inference.
   - Report IC as descriptive until then.
   - Needs a spec for the dependence structure and a simulation showing calibrated size under overlapping labels.

**Recommended order:** M-03 (mechanical, low risk) → M-04 → H-12, which interacts with the Stage 2D multiple-testing registry (Steps 15–17).

## 19. Classification across the five axes

| Item | Software | Mathematics | Statistics | Quantitative model | Research governance |
|---|---|---|---|---|---|
| A1 naked leg | **Correct**: every exception is routed; explicit incident states; restart-durable disable; no retries | n/a | n/a | **Correct, conservative**: unwind from authoritative account state; collateral moves only when flat (R3 repay-first) | Incidents are durable rows; MANUAL_REVIEW requires a human |
| A2 drawdown guard | **Correct** after R1 (fail-open at equity ≤ 0 closed); enforced before any transfer or order | **Correct** after R2 (peak on completed pairs only); boundary verified exactly | n/a | Equity definition = documented sub-portfolio scope; open-leg marks exclude borrow, so slightly optimistic for shorts | **Owner confirmation needed**: the equity formula (§A2) and the dry-run/live journal partition (risk 7) |
| A3 exit min-order | **Correct**: refused before any order; position preserved; incident recorded | Exact minNotional boundary tested (= closes, < refuses) | n/a | Dry-run simulator has no filters, so nothing is claimed in dry run | — |
| A4 dry-run fills | **Correct**: labels persisted end to end | Fill / fee arithmetic independently verified | n/a | More realistic (spread paid), still **no depth, no borrow**; both labelled, not modelled. No parameter tuned | Unknown borrow is `UNAVAILABLE`, never a silent zero |
| A5 migrations | **Correct**: startup refusal on pending or unverifiable schema; 0012/0013 not applied | n/a | n/a | n/a | The operator must run `migrate`; nothing is applied automatically |
| A6 reconciliation | Verified by existing tests; no code change | n/a | Affected trades are excluded from clean parity (tested) | n/a | **Backfill deferred** (open item) |

**Statistics:** Phase 1 makes no statistical claim. No research statistic, threshold or methodology was changed, and Track C is plan-only (§18).

## Final status

**CONDITIONALLY_READY_FOR_PHASE_2_DCA**, superseded by the master programme's own Phase 1 verdict. See `QUANT_BOT_POST_AUDIT_COMPLETION_REPORT_2026-10-02.md` §8:

- Pairs live safety is CONDITIONALLY_READY for continued DRY_RUN soak only.
- The open condition is the owner decision on the A2 mark-price convention.
- Live capital remains BLOCKED.
