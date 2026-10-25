# LTHP-1 — Ladder-Triggered Hedge Protection: Exploratory Protocol (registered before any run, 2026-10-25)

**Authorisation:** owner, 2026-10-25, option (b) of `LTHP_DCA_AUDIT_AND_PROTOCOL_PROPOSAL_2026-10-25.md` §J: *"go with option b"*.

**Classification: EXPLORATORY.** The 2022–2026 GBP/JPY history was already used to design the strategy (CRRP-2026-10-02; the full-ladder losses motivated LCEX-1 and the stop rule). Nothing here is independent evidence.

**Binding limits:**
- this study may **not** select or inform K;
- it may **not** amend or replace `FULL_LADDER_STOP_RULE_PRE_REGISTRATION_2026-10-18.md` or influence its 2026-10-18 → 2027-01-17 evaluation;
- it grants **no** production, paper-engine or live use.

This file's SHA-256 is recorded in the governance log before the first run. Any change is a new registration.

## 1. Data and engine
- **Data:** GBP/JPY 1 h MT5 bars, frozen at `FX_RESEARCH_DATA_CUTOFF` (2026-10-16 08:00 UTC). Replay window 2022-08-10 08:00 → cutoff, identical to the baseline survival run `research_data/dca_survival/2026-10-16_0800`.
- **Engine:** `bot/research/fx_survival_replay.run_replay`, extended with an optional `hedge_policy` (default `None` = the unchanged baseline).
  - Baseline equivalence must hold exactly: same cycles, fills, ledger and equity rows as the saved run. Otherwise the study stops.
- **Costs (primary):** the baseline model: MT5 per-bar spread, broker's current swap (long +9.69 / short −22.47 points/lot/day, triple Wednesday), commission $0 (unavailable), slippage 0.
- **Cost stress (one, declared):** spread ×2, slippage 0.5 pip, commission $3.50/lot/side.
- **Account:** ESMA 1:30, 50 % close-out, margin call 100 %, negative-balance protection.

## 2. Arms (no others will be run in LTHP-1)
| Arm | Rule |
|---|---|
| **A0 BASELINE** | unchanged strategy |
| **A1 STOP5** | at the close of the bar in which fill 5 executed, unless the strategy's own EXIT is pending, queue an EXIT (reason `FIFTH_FILL_STOP`) at the next open |
| **A3 HEDGE_HOLD** | same trigger/timing as A1, but open a SHORT of **exactly the basket's lots** (100 % of measured units) at the next open (sells at bid). See the hedged-basket rules below. |
| **A4 HEDGE_REMOVE** | as A3, plus a single objective removal (below) |

**A3 hedged-basket rules:**
- the trailing-profit rule is disabled while hedged;
- the cycle's existing 72-candle time exit closes **both** legs at the next open;
- one hedge attempt per cycle. If free margin cannot fund it, record `HEDGE_REJECTED` and the cycle continues unhedged.

**A4 removal rule:**
- remove the hedge at the next open after the first bar close ≥ hedge entry bid + 1.0 × ATR(14). ATR is the strategy's causal `compute_causal_atr_series`.
- at most one removal per cycle; never re-hedged;
- after removal the cycle follows the unchanged strategy rules (trailing / time exit);
- if removal never triggers, the hedge stays until the time exit.

The ladder ends at fill 5, so "ladder continues" and "ladder suspended" are identical and are not separate arms.

**Order and timing (all arms):**
- decisions at bar close, executions at the next bar open;
- exits execute before fills; an exit pending for the same open as a hedge cancels the hedge;
- no partial fills (1 h bars, no depth): all-or-nothing, labelled.

## 3. Margin models (accounting assumption, not arms)
The broker's hedged-margin rule (`margin_hedged`) is unverified.
- **M-GROSS** (primary, conservative): each leg is charged full ESMA margin.
- **M-ONE-LEG** (sensitivity): a hedged pair is charged the larger leg only.

Both are run for A3/A4; conclusions are restricted to these models.

## 4. Capital levels
- **$10,000:** primary. No add-on was margin-refused in the baseline.
- **$5,000 and $3,100:** secondary survival.

## 5. Metrics
**Primary:** total net P&L after costs, marked to market at the data end, at $10,000 (M-GROSS), arm vs A0.

**Co-primary (risk):**
- max intrabar equity drawdown (−$ and % of running peak);
- worst single-cycle net.

**Also reported for every arm:**
- trading: net return; full-ladder event count; hedges opened / rejected / removed; hedge P&L, spread, carry and commission;
- exposure and margin: max gross and net exposure; peak margin and min margin level; margin calls and close-outs;
- risk: cycles reaching each level; unresolved positions at the end; per-year net; profit concentration (top-5 cycles' share).

## 6. Statistics (exploratory; nominal p-values are descriptive only)
- **Unit:** calendar month (UTC).
- **Statistic:** d_m = arm net − A0 net per month. Both arms are equity changes on the same price path, so they are paired.
- **Test:** one-sided sign-flip permutation of the monthly differences (10,000 flips, seed 20261025). H0: mean d ≤ 0, i.e. the arm is not better.
- **Multiplicity:** Holm across the 3 arm comparisons at the primary capital/margin model.
- **Calibration (run before reading arm results):** the false-rejection rate of the test on AR(1) symmetric null monthly series (ρ = 0, 0.3, 0.6; 2,000 simulations each, n = observed month count) is reported. If it exceeds 0.07 at ρ = 0.3, p-values are reported as unreliable.
- **Dependence:** month-level dependence is acknowledged. Shared macro events make the effective sample smaller than the month count.

## 7. Interpretation rules (fixed now)
- No arm may be called "validated", "confirmed" or "recommended for production".
- Possible readings:
  - **"Consistent with lower tail risk"**: max drawdown and worst cycle both improve under M-GROSS at $10,000.
  - **"Consistent with higher net P&L"**: Holm-adjusted p < 0.05, with the stated caveats.
  - **"No difference / worse"**: otherwise.
- If A3 ≈ A1 within costs, the hedge adds nothing over a stop (expected from the audit's mathematics).
- Any next step must be a new forward registration. It cannot overlap the full-ladder stop-rule window unless the owner records how the two interact.
