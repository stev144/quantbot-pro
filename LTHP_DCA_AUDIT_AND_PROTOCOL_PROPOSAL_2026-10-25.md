# LTHP-DCA — Ladder-Triggered Hedge Protection: Audit and Protocol Proposal (2026-10-25)

**Status: AUDIT COMPLETE · IMPLEMENTATION BLOCKED (owner decision required) · no experiment run · no code changed.**

- Research-only; no live execution, DRY_RUN, credentials or production strategy touched.
- Section F (Results) is deliberately empty: running the arms on the GBP/JPY history would breach an existing preregistration (§H, blocker B1).

---

## A. Configuration reconciliation (verified from code and saved run files)

| Question | Verified answer | Evidence |
|---|---|---|
| Engine behind the $3,100 failure and the $5,000 survival | **The same engine and configuration**: `bot/research/fx_survival_replay.py::run_replay`, run `research_data/dca_survival/2026-10-16_0800/` (report.json + `runs/gbpjpy_{3100,5000,10000}_*.csv`) | `report.json["capital_runs"]` keys `gbpjpy_3100/5000/10000`; identical `strategy` block |
| Strategy | QC-1 forex DCA, GBP/JPY **LONG only**. In-cycle logic: `fx_fold_runner.decide_in_cycle()` (profit activation 0.5 %, 0.2 % trailing, **72-candle time exit**, ATR-scaled ladder trigger) | `fx_survival_replay.py:63-68, 704-707`; `fx_fold_runner.py:221-295` |
| Ladder | **5 fills, multipliers 1 : 2 : 4 : 8 : 16** (LCEX-1, 2026-10-19). Levels 2–5 spacing = p40/p60/p75/p85 of each walk-forward fold's training MAE, scaled by causal ATR. **No level can exist after the 5th fill** (`lvl_idx < len(base_pips)` = 4). | `fx_fold_runner.py:36, 278-294`; `fx_survival_replay.py:567` (`DCA_LADDER_EXHAUSTED` at fill 5) |
| Sizing | Entry = $3,000 notional floored to the broker lot grid (→ **0.02 lots**); add-ons exact multiples of the executed entry. **Fixed size: does NOT scale with account capital.** Full ladder = 0.62 lots = 62,000 GBP (≈ $79.6k notional in Jul 2024). | `fx_survival_replay.py:507-524`; fills of cycle 257 |
| Leverage and margin | ESMA retail 1:30 (GBP/JPY), margin = units × base→USD / 30 **per open position**; 50 % margin close-out at the bar's adverse extreme; margin call warning at 100 %; negative-balance protection | `fx_survival_replay.py:70-74, 428-438, 634-689` |
| Period | **2022-08-10 08:00 → 2026-10-16 07:00 UTC** for all three capital levels. *Not 2021*: only the EUR/USD leg of the separate 5-pair portfolio run starts in 2021-08. | `gbpjpy_*_rows.csv` first/last timestamps |
| Costs | MT5 per-bar spread history (bar minimum, optimistic); swap from the broker's CURRENT rates (long +9.69 / short −22.47 points/lot/day, triple Wednesday), labelled ESTIMATED; commission UNAVAILABLE (charged $0); slippage 0 | `report.json["cost_labels"]`; broker snapshot 2026-10-21 |
| Unresolved cycles | 1 open cycle at the data end in every run, marked to market and never dropped | `outcomes: OPEN_UNRESOLVED_AT_DATA_END: 1` |
| Meaning of "fifth fill" | The 5th executed order of a cycle (levels 1–5). The level-5 add-on executes at a bar open, after a bar-close trigger; the fill is confirmed at that open. **It is the last possible fill.** | `try_fill()`; `decide_in_cycle()` |
| Can levels continue past the 5th fill? | **No.** After fill 5 the only possible actions are the trailing-profit exit and the 72-candle time exit. | as above |
| Hedging account? | The broker account is **`margin_mode = 2` = MT5 RETAIL_HEDGING**: simultaneous LONG and SHORT on one symbol are allowed. The *replay engine* holds **one LONG position per pair** and cannot represent a hedge. | `report.json["account_rules"]["broker_account"]`; `_PairState.cycle` (single) |
| Hedge margin rule | **UNKNOWN.** MT5's `margin_hedged` / `margin_hedged_use_leg` were never recorded in the broker snapshot, and MT5 is currently logged out ("Authorization failed"). Possible range: one leg only (≈ $2,652 for the full ladder) up to both legs (≈ $5,303). | snapshot fields for GBP/JPY: `trade_contract_size`, `currency_margin` only |

### Outcomes, reconciled
| | $3,100 | $5,000 | $10,000 |
|---|---|---|---|
| Ending equity | $6,483 | $11,856 | $16,856 |
| Cycles / reaching fill 5 | 515 / 37 | 517 / 46 | 517 / 46 |
| Add-ons refused (free margin) | **19** | 0 | 0 |
| Margin calls / close-outs | 13 / **1** (2022-09-26, −$2,362) | 1 / 0 | 0 / 0 |
| Worst % drawdown (equity incl. unrealised, % of running peak) | **−90.7 %** (2023-01-13, equity $352) | **−65.4 % at bar close; −69.9 % intrabar** (2022-09-26, peak $5,669 → $1,705) | −31.7 % close |
| Largest $ drawdown | −$4,492 | −$4,920 (2024-08-05) | −$4,920 |
| Min margin level | 67 % | 91 % | 321 % |

- **Why $5,000 "survived" and $3,100 did not.** It isn't strategy quality: the position sizes are fixed, so the larger account simply had more margin headroom.
  - At $3,100 the free margin could not fund 19 add-ons.
  - The UK mini-budget move then triggered an ESMA close-out.
  - From there the paths diverge, which is why there are 515 vs 517 cycles.
  - $5,000 and $10,000 trade identically. Only the drawdown percentage differs.
- **The "≈ 69 %"** is the $5,000 run's **intrabar** worst-equity drawdown (−69.9 %, 26 Sep 2022). It is equity-based, including unrealised losses, not balance-based.
  - Recovering from it needs **+232 %** (5,669 / 1,705 − 1).
  - The −$4,920 dollar maximum is a different episode (BoJ, Aug 2024), with a lower percentage because the peak was higher.
- **"Some cycles continue through many ladder levels and eventually recover"** cannot come from this engine.
  - Its maximum is 5 fills, and the longest cycle lasted 145 h, because the 72-candle time exit closes at the next open after 72 h.
  - **The source of that observation is not identified** (live/demo MT5 trades? the earlier 7-level ladder? the mentor's bot? crypto?). That evidence is missing.
- **Where the risk sits** (descriptive, in-sample, $5,000 run):

| | Cycles | Net |
|---|---|---|
| Cycles with ≤ 4 fills | 471 | **+$20,700** |
| Cycles reaching fill 5 | 46 | **−$13,844** (30 % profitable; 41 of 46 end at TIME_EXIT) |
| Total | 517 | +$6,856 |

  This is exactly the pattern that motivated the 5-fill cap and the full-ladder stop-rule preregistration, so it is selection-contaminated evidence, not a discovery.

## B. Hypothesis — and what the mathematics says first
**H (provisional):** opening an opposite (SHORT) position after the confirmed 5th fill reduces risk without unnecessarily sacrificing recovery.

**Key result (G3, hand-verified below).** On a hedging account, a SHORT of the same size on the same symbol **freezes the basket's P&L**: each further price move adds the same amount to one leg and subtracts it from the other. The remaining differences from simply closing the basket at that moment (a stop-loss) are:

1. extra spread on the hedge leg (opening and closing);
2. negative net carry: short −22.47 + long +9.69 = **−12.78 points/lot/day ≈ −$5.2/day** for 0.62 lots, ×3 on Wednesdays;
3. currency-translation drift: the frozen loss is in JPY, so its USD value moves with USD/JPY;
4. **extra margin**, if the broker charges both legs;
5. the *option* to remove the hedge later. Removing it is economically the same as **re-entering** the LONG exposure at the removal price.

So, absent removal, **"full hedge at the 5th fill" ≈ "stop-loss at the 5th fill + costs"**. With removal, it ≈ "stop-loss, then re-buy on a recovery signal". A hedge cannot create a recovery that the stop-plus-re-entry would not have.

**Why it might help:** it caps the loss of the cycles that keep falling after fill 5 (41 of 46 such cycles ended at TIME_EXIT).

**Why it might fail:**
- it locks in the loss of the cycles that would have recovered (5 of 46 reached the trailing-profit exit);
- costs and carry;
- under gross margin it adds about $2.65k of margin exactly when equity is lowest. At $5,000 in Sept 2022 the hedge order would likely be **rejected** for margin;
- re-entry whipsaw (Arm 4).

## C. State-transition specification (proposed)
`FLAT → L1 … L4 (DCA allowed) → L5_CONFIRMED (fill executed at bar open t) → HEDGE_PENDING (market SHORT, executes at next bar open t+1; 1 h bars cannot show the path inside bar t) → HEDGED → [Arm 4 only] REMOVAL_PENDING → UNHEDGED_AFTER_REMOVAL → CLOSED`.

- **Pending DCA orders:** none can exist after L5_CONFIRMED (no level 6), so cancel / suspend / continue are **identical**.
- **Exit beats hedge:** if the cycle's own exit (trailing or time) is pending for the same open, the cycle closes and the hedge order is cancelled. This is recorded, never silently dropped.
- **Hedge rejected** (insufficient free margin under the declared margin model): record `HEDGE_REJECTED`; one attempt only; the cycle continues unhedged.
- **Partial fills:** not representable (1 h bars, no depth). All-or-nothing, labelled.
- **While HEDGED:**
  - the trailing-profit rule on the LONG WAP is disabled, because it would just realise the frozen loss;
  - the existing **72-candle time exit closes both legs** at its next open.
- **At the data end:** both legs are marked to market and reported as OPEN_UNRESOLVED.

## D. Experimental arms
| Arm | Status | Reason |
|---|---|---|
| 0 Baseline | ready | the existing run above |
| 1 "No further additions after fill 5" | **REDUNDANT = Arm 0** | the ladder already ends at fill 5. The meaningful no-hedge control is **1′: close the basket at the next open after fill 5 (stop)**. It is a separately justified design, because §B shows the hedge ≈ this stop. |
| 2 Hedge + ladder continues | **IDENTICAL to Arm 3** | no further ladder levels exist |
| 3 Hedge + ladder suspended | proposable | 100 % hedge (equal lots, same symbol), held until the time exit |
| 4 Hedge + objective removal | proposable | primary rule (declared once, never re-chosen): remove at the next open after a bar close ≥ hedge price + 1.0 × ATR(14) (the causal ATR the strategy already uses); at most one removal per cycle, never re-hedged; the existing time exit still applies; if never triggered, the hedge stays until the time exit |

The workable grid is therefore **3–4 arms: 0, 1′, 3, 4**.

## E. Mathematical review — hand calculation on the real worst cycle (#257, 2024-07-30 → 08-02, $5,000 run)
- **Fills** (lots @ executed ask): 0.02 @ 199.126 · 0.04 @ 198.781 · 0.08 @ 198.199 · 0.16 @ 197.199 · 0.32 @ 196.108.
  - Total 0.62 lots = 62,000 GBP.
  - WAP = (Σ lots × price) / 0.62 = **196.92916**, matching the engine's `wap` 196.929161.
- **Hedge** at the next open, 2024-07-30 22:00: SHORT 0.62 @ bid 195.922. USD/JPY 152.694; spread 10 points (0.010).
  - LONG liquidation value then: (195.922 − 196.92916) × 62,000 = **−62,444 JPY = −$408.95**.
- **Margin per leg:** 62,000 × 1.28309 (GBP/USD) / 30 = **$2,651.7**.
  - Gross model: **$5,303.4**. One-leg model: $2,651.7.
  - The engine's `peak_margin_usd` is 2,652.3, measured at the fill-time rate.
- **At the time exit** (2024-08-02 11:00 open, bid 189.514, USD/JPY 148.799):
  - LONG = (189.514 − 196.92916) × 62,000 = −459,740 JPY;
  - SHORT closes at ask 189.524: (195.922 − 189.524) × 62,000 = +396,676 JPY;
  - **combined −63,064 JPY = −$423.8** before carry.
  - Carry over 4 rollover-days (one triple): (+9.69 − 22.47) × 0.001 × 100,000 × 0.62 × 4 = **−3,169 JPY ≈ −$21**.
  - The −$15 difference from the stop value is spread plus USD/JPY translation (152.69 → 148.80).
- **Baseline actual:** **−$3,071.73** (TIME_EXIT). The hedge would have saved ≈ $2,630 on *this* cycle.
  - This is the cycle the idea was built around (hindsight / selection). It demonstrates the arithmetic, not a benefit.
- These figures become the known-answer tests before any engine change: WAP, frozen-P&L invariant, carry, margin, and the hedge-equals-stop identity within costs.

## F. Results
**Not produced.** Running Arms 1′/3/4 on 2022-08 → 2026-10 GBP/JPY is blocked by B1. No numbers are fabricated.

## G. Statistical evaluation (proposed design, for when it is permitted)
- **Unit:** each cycle that reaches fill 5.
  - Compare paired outcomes per event: D_k = arm net − baseline net. Use the $10,000 capital level for the paired comparison, so margin rejections do not make the paths diverge.
  - Survival at $5,000 and $3,100 is a secondary, descriptive measure.
- **Dependence:** events within 7 days of each other form one cluster (shared macro shock). Use a cluster sign-flip test.
- **Multiplicity:** 3 arm-vs-baseline comparisons, Holm-corrected.
- **Primary metric:** paired mean net per full-ladder event. Co-primary risk metric: worst-event loss and max equity drawdown.
- **Classification:** all of the 2022–2026 history is **pseudo-holdout** (CRRP-2026-10-02). The full-ladder losses already motivated LCEX-1 and the stop rule, so any historical result is **EXPLORATORY only**.
- **Power:** about 46 qualifying events in 4.2 years ≈ 11 a year.
  - A 90-day forward window gives about 3 events: **no usable power**.
  - A confirmatory forward test needs roughly 3–4 years for about 40 events.
  - This is stated now so it cannot be relaxed later.

## H. Five-gate compliance
| Gate | Status | Evidence / blocker |
|---|---|---|
| G1 Research integrity | **BLOCKED** | **B1:** `FULL_LADDER_STOP_RULE_PRE_REGISTRATION_2026-10-18.md` §7: *"Do not implement any stop in the live or paper simulation code"* before 2027-01-17 (GBP/JPY); §5: pre-2026-10-18 data may not be used to tune or validate it. Arms 1′/3 are a full-ladder stop (immediately or ≈ by §B), and Arm 4 is stop + re-entry. Running them would implement and historically evaluate exactly that rule. |
| G2 Quant / trading | **PARTIAL / BLOCKED** | Causal next-open design specified. **B2:** the hedge-margin rule is unknown (`margin_hedged` never recorded; MT5 logged out). The engine supports one position per pair, so an isolated two-leg extension is needed. Commission is unknown. |
| G3 Mathematics | **PASS (hand-verified)** | §E reproduces the engine's WAP and margin to 4–6 significant figures; frozen-P&L and carry identities derived. Code tests are not yet written (no code). |
| G4 Statistics | **DESIGNED, NOT RUN** | §G: paired, clustered, Holm-corrected, power stated (insufficient for confirmation within the stop-rule window) |
| G5 Software | **NOT STARTED** | state machine §C specified; tests listed; nothing implemented, by design |

## I. Recommendation
**Do not implement now. The idea isn't rejected on its merits; it is mathematically a variant of an experiment you have already preregistered.**

- A 100 % same-symbol hedge at the 5th fill is a stop-loss at the 5th fill plus extra costs. Its only distinct element is the removal (re-entry) rule.
- The full-ladder stop is already preregistered for a forward test that ends 2027-01-17, with a ban on implementing or historically validating it before then.
- The hedge may add margin risk rather than reduce it, depending on an unverified broker rule. At $5,000 during Sept 2022 a gross-margin hedge order would probably have been rejected.
- Risk reduction (smaller full-ladder losses), profitability (net P&L) and survivability (margin path) are three different questions. None of them is answered by history already used to design the strategy.

## J. Next steps (smallest first)
1. **Owner decision on B1**, one of:
   - (a) defer LTHP until the stop-rule window closes (2027-01-17);
   - (b) explicitly authorise an EXPLORATORY historical run of Arms 0/1′/3/4, recorded as unable to select K or alter the stop-rule registration;
   - (c) register Arm 4 as its own forward test, accepting the power limit in §G.
2. **Log in to MT5** so `symbol_info("GBPJPY").margin_hedged` / `margin_hedged_use_leg` can be read (clears B2).
3. **Name the source** of the "long-lived cycles that recover after many levels" observation, so it can be audited. It is not this engine.
4. **Housekeeping found during the audit:** the stop-rule registration's motivation (§1) describes 7-fill cycles. Its rule ("fills == ladder_depth") still applies to today's 5-fill ladder, but the K thresholds were sized on 7-level losses. The owner should confirm the registration still means what was intended, as a new dated file if it changes, never by editing it.
