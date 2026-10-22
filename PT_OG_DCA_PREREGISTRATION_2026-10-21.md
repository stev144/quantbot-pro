# PT-OG-DCA-1 — Preregistration (DRAFT, not signed)

**Status:** DRAFT, revision 2 (2026-10-21). Not in force until the owner signs §9.
- **Route B (owner, 2026-10-21):** descriptive-only. This program makes **no statistical claim** and reaches at most
  the D-7 level **OBSERVATION**.
- **T5 dropped** (owner, 2026-10-21): 5 arms remain.
- No experiment code exists yet. Nothing here changes QC-1, QC-2, `DRY_RUN`, or live execution.

## 1. Baseline (unchanged, the control arm)
QC-1 LONG GBP/JPY as run by `bot/research/fx_survival_replay.py`:
- Exit: gross 0.5 % profit activation on the weighted-average entry price (WAP), then a 0.2 % trailing exit, plus a
  72-bar time exit.
- Ladder: 5 fills, 1:2:4:8:16, ATR-scaled spacing, NEXT_OPEN fills.
- Re-entry: unconditional while flat, solvent and with no order pending.
- Account: ESMA 1:30, 50 % close-out, negative-balance protection.
- Reference result (development history to 2026-10-16 08:00 UTC, $5,000 account): ending equity **$11,856.41**,
  reconciled to the ledger 2026-10-21.

## 2. Arms (5; D-3 cap respected)
Every arm keeps the §1 settings except the change listed. The re-entry rule is the baseline's own (decision D2), so
any difference comes from the exit rule only.

| Arm | Change from the baseline | How the value was set |
|---|---|---|
| T1 | Net target 0.25 % of deployed notional, replacing activation + trail; 72-bar time exit kept | Half the baseline's 0.5 % |
| T2 | Net target 0.50 % of deployed notional (as T1) | Equal to the baseline's 0.5 % |
| T3 | Net target 1.00 % of deployed notional (as T1) | Double the baseline's 0.5 % |
| T4 | Net target $15 (as T1) | 0.5 % of the nominal $3,000 first fill |
| H6 | Time exit **84 bars** instead of 72; baseline activation + trail kept; no net target | Owner rationale: "room for losing ladders to recover" (72 + 12 h) |

Dropped or excluded:
- **T5 ($30):** dropped as the redundant second fixed-dollar arm.
- **ATR-scaled targets:** a separate hypothesis.
- **Removing the time exit:** excluded (decision D1).

## 3. Accounting
- **Net P&L:** gross − spread − slippage − commission ± swap, in USD at bar rates.
- **Deployed notional:** the sum of each fill's USD notional at its fill price.
- **Target check (T1–T4):** at the bar close, on the net value of closing the whole position (exit spread and
  accrued swap included). The exit fills at the next open. Trigger value versus realised value is reported for every
  target exit.
- **Pending orders:** a target hit schedules no further ladder add. The baseline places no resting orders.
- **Open cycles:** labelled `OPEN_UNRESOLVED_AT_DATA_END`, valued at liquidation and always reported. They are
  never dropped.
- **Missing costs:**
  - Commission is UNAVAILABLE (§8, G4).
  - Swap uses the current snapshot for all history.
  - Spread is the MT5 bar minimum (24.7 % of bars record 0).
  - Every metric is therefore also reported under the existing `all_costs_realistic` stress scenario.

## 4. What is reported
Reported for each arm and the baseline, at $3,100, $5,000 and $10,000, under both baseline and stressed costs:
- ending equity and net P&L;
- maximum drawdown as −$X (−Y %);
- margin close-outs and insolvency;
- cycles, split by fill depth and by exit reason;
- time-exit P&L;
- spread, slippage, commission and swap totals;
- the swap share of net P&L.

Arm-specific additions:
- **H6:** the P&L of cycles that would have time-exited at 72 bars, measured from the 72-bar mark to their actual
  exit. Did the extra 12 h help or deepen them?
- **Every arm:** the weekly arm − baseline difference series (mean, median, worst and best week), and the
  contribution of the 3 largest single weeks to the total difference.

## 5. Inference — none (route B)
- **No p-value is computed or reported.** The calibration (§5a) shows the only admissible family of tests
  over-rejects in the arm's favour on DCA-shaped data, and has no usable power.
- A time-block bootstrap interval (`bot.quant_core.inference.time_block_bootstrap_mean_ci`, block 288 / 336 bars)
  may be shown. It is labelled "DESCRIPTIVE — uncalibrated for this distribution shape, not a test".
- **Language:** "arm X ended $Y above/below the baseline over this window". Never "significant", "edge", "validated"
  or "better strategy".

### 5a. Calibration record (2026-10-21) — why there is no confirmatory test
Code `bot/research/pt_og_dca_calibration.py` (10 tests); outputs `research_data/pt_og_dca/calibration_2026-10-21.json`
and `calibration_addendum_2026-10-21.json`.
- **Rules applied.** Signed decisions D-2 (block permutation), D-10 (block 288/336) and D-3 (B = 999, Holm, ≤ 5
  hypotheses). The test was a paired block sign-flip on hourly net-equity differences, 2,000 replications.
- **Size FAILS:**
  - skewed zero-mean lumps: 7.5–13 % rejection at nominal 5 %, nearly all "arm ahead";
  - AR(0.99) persistence: 9–13 %.
  - A bootstrap-t candidate also fails (6–11 %). It passes only on iid data.
- **Semi-real null** (the baseline's own realised path): ≈ 0.1 % size at 12–18 months, because P&L is negatively
  dependent at the multi-week scale.
- **Power:**
  - at most 5 % for $5–20 per week, even at 18 months;
  - detection needs ≥ $40 per week, which exceeds the baseline's whole average of $31.6 per week.
- **What would allow a statistical claim (route C, not authorised):** a multi-pair redesign with time-clustered
  inference and its own calibration.

## 6. Reading rules (replace pass/fail)
1. **Ceiling.** The strongest possible verdict is OBSERVATION (D-7). No arm can change QC-1, QC-2, any
   configuration or live behaviour on this evidence.
2. **Risk read first.** An arm with more margin close-outs or a deeper maximum drawdown than the baseline (at
   $5,000) is recorded as "risk worse", whatever its P&L.
3. **Tail concentration.** If the 3 largest weeks explain more than half of an arm's difference, the result is
   recorded as "driven by a few events".
4. **Cost robustness.** An arm that is ahead under baseline costs but behind under `all_costs_realistic` is
   recorded as "cost-fragile".
5. **What a favourable observation permits.** At most, a proposal to design route C. Nothing else.

## 7. Data use
- **Development history (to 2026-10-16 08:00 UTC):** one descriptive run of all 5 arms; all arms reported;
  classified pseudo-holdout.
- **Forward window:**
  - sealed from the signing date;
  - not read before QC-2 closes (2027-01-17);
  - **read once**, 12 months after signing;
  - no interim looks and no extensions.
- **Invalidation:** reading the window early invalidates it.
- **Untouched:** QC-1, QC-2, `DRY_RUN`, live execution, and the full-ladder stop rule test.

## 8. Five gates (`FULL_RESEARCH_PIPELINE_FIVE_GATE_AUDIT_2026-10-03.md` §4)

| Gate | Status | Requirement / blocker |
|---|---|---|
| G1 Software | Satisfiable | Tests must fail on: a missed target, a ladder add scheduled after a target, open cycles dropped, and H6 exiting at bar 72. Default settings must reproduce $11,856.41. |
| G2 Mathematics | Satisfiable | Known-answer test of the net target in USD. |
| G3 Statistics | **BLOCKED for any statistical claim** | §5a. Route B makes no claim, so the program can run, but it is capped at OBSERVATION. |
| G4 Quant / trading | **PARTIAL** until the broker commission schedule is known | Historical swap is unavailable. Every result carries the cost labels of §3. |
| G5 Research integrity | Satisfiable | Fixed arms, a single sealed read, all arms reported, pseudo-holdout labels. |

## 9. Open items before signing
- [x] Synthetic calibration — done 2026-10-21 (§5a)
- [x] Route decision — B, descriptive only (owner, 2026-10-21)
- [x] D-3 cap — T5 dropped (owner, 2026-10-21)
- [ ] Commission schedule (account type, or the deal record of a 0.01-lot demo trade)
- [ ] Approval of the code change (`fx_survival_replay.py`, `test_fx_survival_replay.py`) — after the commission item
- [ ] Owner signature and date (opens the forward window)
