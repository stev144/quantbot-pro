# LTHP-1 Results — Ladder-Triggered Hedge (EXPLORATORY, 2026-10-25)

**Protocol:** `LTHP_1_PREREGISTRATION_2026-10-25.md`, SHA-256 `42e517b1…16a`. It was registered before any run, and the runner verifies the hash.

**Report data:** `research_data/lthp_study/lthp1_report_20261025T095539Z.json` (runtime 53 s). Reproduce with `python manage.py run_lthp_study`.

**Classification: EXPLORATORY.** This is pseudo-holdout GBP/JPY history, 2022-08-10 → 2026-10-16, already used to design the strategy.
- **Binding:** these results may not select or inform K of `FULL_LADDER_STOP_RULE_PRE_REGISTRATION_2026-10-18.md` and grant no production use.
- The audit and design are in `LTHP_DCA_AUDIT_AND_PROTOCOL_PROPOSAL_2026-10-25.md` (sections A–E).

## F. Results

### Primary capital $10,000 (no margin constraint binds; GROSS = ONE_LEG)
| Metric | A0 Baseline | A1 Stop at fill 5 | A3 Hedge, hold | A4 Hedge, remove at +1 ATR |
|---|---|---|---|---|
| Net P&L (MTM at end) | **+$6,856** | **+$6,954** | +$3,979 | +$5,122 |
| Max equity drawdown, bar close | −$4,920 (−34.7 %) | **−$2,760 (−24.2 %)** | −$3,682 (−30.3 %) | −$6,463 (−46.7 %) |
| Max drawdown, intrabar | −37.2 % | **−24.4 %** | −30.5 % | −48.7 % |
| Worst single cycle | −$3,072 | **−$1,048** | −$1,440 | **−$3,556** |
| Longest drawdown | 10,052 h | **6,284 h** | 22,506 h | 22,541 h |
| Completed cycles (cycles reaching fill 5) | 516 (46) | 550 (48) | 512 (46) | 516 (46) |
| Hedges opened / removed | — | — | 46 / 0 | 46 / 30 |
| Hedge carry (swap) / extra spread | — | — | −$709 / $295 | −$368 / $276 |
| Max gross notional | $85.0k | $84.6k | **$170.0k** | **$170.0k** |
| Min margin level (GROSS) | 321 % | 354 % | 169 % | 158 % |

**Cost stress** (spread ×2, 0.5 pip slippage, $3.50/lot/side): A0 +$4,031 · A1 +$4,193 · A3 **+$380** · A4 +$1,570.

### Survival at smaller capital
| | A0 | A1 | A3 GROSS | A3 ONE_LEG | A4 GROSS | A4 ONE_LEG |
|---|---|---|---|---|---|---|
| **$5,000** net / intrabar DD | +$6,856 / −69.9 % | +$6,954 / **−44.0 %** | +$4,645 / −54.3 % | +$3,979 / −51.7 % | +$6,512 / −65.9 % | +$5,122 / **−76.2 %** |
| $5,000 margin calls / close-outs | 1 / 0 | 0 / 0 | 2 / 0 | 0 / 0 | 0 / 0 | 1 / 0 |
| $5,000 hedges rejected for margin | — | — | **6 of 46** | 0 | 4 | 0 |
| **$3,100** net / intrabar DD | +$3,383 / −90.8 % | +$8,073 / **−50.4 %** | +$4,190 / −90.8 % | +$5,744 / −72.2 % | +$4,428 / −90.8 % | +$5,565 / −77.2 % |
| $3,100 close-outs / margin calls | **1** / 13 | **0** / 0 | **1** / 13 | 0 / 5 | **1** / 13 | 0 / 3 |
| $3,100 hedges rejected for margin | — | — | **34 of 37** | 2 | 34 | 1 |

### What the numbers show
1. **A hedge is a stop that blocks re-entry and pays carry.** This was predicted by the audit's mathematics.
   - A1 and A3 trigger at the same moment and lock the same loss.
   - A3 then keeps the basket frozen until the 72-candle time exit. Meanwhile it pays −$709 of short swap and $295 of extra spread, and opens no new cycles: 512 cycles, against 550 for A1.
   - Net, A3 is **$2,974 behind A1**, with a worse drawdown and worst cycle. A3 is dominated by A1 on every reported metric.
2. **Removing the hedge (A4) makes the tail *worse* than doing nothing.**
   - Its worst cycle is −$3,556, against −$3,072 for the baseline, and its drawdown is −$6,463 / −46.7 %.
   - The +1 ATR rule re-exposed the basket on bounces that then failed: 2024 net −$2,282, the BoJ carry-unwind period.
3. **Gross margin can make the hedge unavailable exactly when it matters.**
   - At $3,100 under M-GROSS, 34 of 37 hedges were rejected, so the account was liquidated just as in the baseline.
   - Whether a real broker allows the hedge depends on the still-unverified `margin_hedged` rule.
4. **The plain stop at fill 5 (A1)** had the same P&L as the baseline (+$97 over 51 months; 20 months better, 20 worse, 11 equal), but a much smaller tail:
   - drawdown −$2,760 vs −$4,920;
   - worst cycle −$1,048 vs −$3,072;
   - **no close-out at $3,100.**

   This is *exploratory and selection-contaminated*: the fill-5 losses that motivated the idea are in this very sample.

## G. Statistical evaluation
- **Unit:** calendar month (51). Paired MTM equity difference, arm − A0, one-sided sign-flip, Holm over 3 comparisons.
- **Registered calibration FAILED:** the false-rejection rate is 0.050 at ρ = 0, **0.0895 at ρ = 0.3** (limit 0.07), and 0.21 at ρ = 0.6. As pre-registered, **the p-values are unreliable** and nothing rests on them.
- For the record:

| Comparison | Nominal p | Holm p | Months better / worse |
|---|---|---|---|
| A1 | 0.49 | 1.0 | 20 / 20 |
| A3 | 0.80 | 1.0 | 19 / 23 |
| A4 | 0.75 | 1.0 | 12 / 29 |

  No arm shows a net-P&L improvement.
- **Dependence:** the 46–48 full-ladder events cluster in a few macro shocks (Sep 2022, Jul–Aug 2024, Aug–Sep 2026), so the effective sample is small.

**Pre-registered readings (§7):**

| Arm | Lower tail risk? | Higher net P&L? |
|---|---|---|
| A1 | **Consistent with lower tail risk** (DD and worst cycle both better at $10k GROSS) | No difference |
| A3 | Consistent with lower tail risk vs A0 | Worse; and A3 ≈ A1 minus costs → **the hedge adds nothing over a stop** |
| A4 | **Worse** (DD and worst cycle both worse) | Worse |

## H. Five-gate compliance
| Gate | Status | Evidence |
|---|---|---|
| G1 Research integrity | **PASS (exploratory)** | Owner authorisation (option b) and the registration hash were logged before running; the runner refuses an altered protocol; data frozen at the FX seal with OHLC and spread SHA-256 in the report; results classified EXPLORATORY with binding limits |
| G2 Quant / trading | **PARTIAL** | Next-open execution, exits before fills, per-bar spread, short-leg swap, margin per model, ESMA close-out of the hedged basket at its worse bar extreme, unresolved positions marked. **Limits:** broker `margin_hedged` unknown (bounded by two models); 1 h bars (no intrabar path, no partial fills); swap from current rates; commission unknown (stress only) |
| G3 Mathematics | **PASS** | Known answers: stop P&L at the next open; **hedge = stop exactly at zero cost**; hedge = stop − one spread × units; short swap × lots × days; margin 2× (GROSS) vs 1× (ONE_LEG); gross = 2× long, net = 0 while hedged; removal P&L; ledger and cycle identities reconciled in every run |
| G4 Statistics | **FAIL (as pre-registered: p-values unreliable)** | Registered calibration 0.0895 > 0.07 at ρ = 0.3. Conclusions rest on descriptive risk and P&L figures only. |
| G5 Software | **PASS** | 17 new tests (`bot/tests/test_lthp_hedge.py`) plus 24 existing survival tests; baseline bit-identical to the saved 2026-10-16 runs at all three capitals; invalid policies refused; one real bug found by a test and fixed (hedge swap missing from the cycle's swap field) |

## I. Recommendation
- **Reject the hedge (A3/A4).** A same-symbol hedge after fill 5 is dominated by simply closing the basket:
  - it locks the same loss, pays carry, doubles gross exposure, and blocks new cycles;
  - its removal rule re-opens risk into failing bounces;
  - under gross margin it is often unavailable when it is needed.
- **Risk reduction ≠ profitability ≠ survivability:**
  - Only the plain stop (A1) looked better on risk here: smaller drawdown and worst cycle, no liquidation at $3,100.
  - It did not improve P&L.
  - It is exploratory, in-sample evidence on the very episodes that motivated it.
- **The stop question already has its proper test:** the preregistered full-ladder stop rule (forward window 2026-10-18 → 2027-01-17). This study may not be used to choose its K or to change it.

## J. Next steps
1. Do not build the hedge into any engine.
2. Let the full-ladder stop-rule forward test run to 2027-01-17 untouched.
   - Note the power limit: about 11 full-ladder events a year, so roughly 3 in 90 days.
   - The owner may want a longer registered window, recorded as a *new dated* registration, never an edit.
3. If useful later: log in to MT5 to record `margin_hedged`, so any future hedge-related research can drop the two-model bound.
