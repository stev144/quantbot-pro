# Decision Sheet (PROPOSAL, NOT SIGNED): OPEN-36 calibration margins and D-3 crypto registration

**Status:** PROPOSED by Claude Code on 2026-10-07 for owner review. **Nothing here is in force.** No code, gate,
registry entry or threshold has been changed. Each item takes effect only when the owner signs it below, edits the
values, or rejects it.

## 0. Why these two, and in which order

They form one dependency chain. Deciding item 2 without item 1 produces a registration that cannot be tested.

```
OPEN-36 (δ, α)  →  D-4 Step-17 null calibration run (compute)  →  zone-V test of registered crypto pairs
                                                     ↘  canonical walk-forward also needs owner fold boundaries
```

- The crypto pairs would be confirmed in zone V with the D-4 statistic (governance 2026-10-04, step 3). That null
  is BLOCKED until it is calibrated.
- Calibration cannot be judged PASS until OPEN-36 fixes δ and α.

## 1. OPEN-36: calibration gate margins

**What it controls:** `bot/quant_core/calibration.py`. A test's rejection rate under a true null is declared
calibrated (size gate) only if the one-sided Wilson upper bound of the observed rate is ≤ 5 % + δ at confidence
1 − α. Coverage gates mirror this for 95 % intervals.

**The cost of each choice:** null replicates needed per scenario, so that a correctly sized test (true rate 5 %)
passes with 80 % power. Computed with `minimum_repetitions`.

| δ (allowed excess over 5 %) | α = 0.05 | α = 0.01 |
|---|---|---|
| 1.0 pt (≤ 6 %) | 3,202 | 5,322 |
| 1.5 pt (≤ 6.5 %) | 1,470 | 2,487 |
| 2.0 pt (≤ 7 %) | 862 | 1,468 |
| 2.5 pt (≤ 7.5 %) | 579 | 976 |

**Proposed (strictest that stays computable):** **δ = 1.0 percentage point, α = 0.05, power 0.80 → 3,202
replicates per scenario.**
- *Why not looser:* a test that rejects 7 % of true nulls inflates family-wise error under Holm. δ = 1 pt is the
  tightest margin that standard practice treats as "correctly sized".
- *Why not α = 0.01:* it raises compute by 66 % for a smaller false-pass risk on the gate itself; α = 0.05 matches
  the project's other one-sided gates.
- *Strictest-possible alternative:* δ = 1.0, α = 0.01 (5,322 replicates).

**Consequences the owner should know before signing:**
- **The recorded D-2 calibration (size 3.3–6.3 %, 9 scenarios) was not run at 3,202 replicates.** It must be
  re-run, and a scenario near 6.3 % would likely **fail** δ = 1 pt. That is a real possible outcome, not a
  formality.
- **The Engle-Granger size checks (worst Wilson upper bound 13.8 % under heavy tails / GARCH) would fail** at any
  δ in the table. The screen's p-values would need a heavy-tail-robust null before they count as calibrated.
- **Compute:** D-4 needs 500 or more replicates of a 4.9 s statistic per scenario, and 3,202 is the bar for a
  *size* claim. A decision on how many scenarios (worlds × innovations) is part of the D-4 compute approval.

**Owner decision:** [ ] accept δ = 1.0 / α = 0.05 [ ] accept δ = 1.0 / α = 0.01 [ ] other: δ = ___ α = ___ [ ] reject

## 2. D-3: register crypto zone-S screen pairs for zone-V confirmation

**Source:** `research_data/selection_screen/crypto_screen_2026-10-07.json`. The 5 nominated pairs (BH q = 0.10 on
zone S, exploratory, canonical-seed eligible):

| Pair | Hedge ratio | Half-life (h) |
|---|---|---|
| AVA/PUNDIX | 1.134 | 32.8 |
| DIA/FIL | 0.674 | 51.4 |
| DIA/IOTX | 0.859 | 47.8 |
| DIA/LPT | 0.842 | 61.9 |
| DIA/ONT | 1.049 | 45.0 |

**Facts that weigh against registering them as-is** (governance 2026-10-07):
- **Co-movement is common in zone S.** Random same-period coin pairs reject Engle-Granger 32.3 % of the time;
  time-shifted pairs 0.4 %. So these nominations show market-wide trend + idiosyncratic mean reversion in
  Nov 2021 – Dec 2022. They are not five independent discoveries.
- **4 of the 5 share DIA.** The family is effectively one bet on DIA. Holm controls FWER under dependence, but the
  power to find more than one independent effect is low.
- **D-3 requires a written economic rationale per hypothesis.** That cannot be supplied by the software and must
  come from the owner. Without it, registration is refused by design.
- **They are not testable yet:** the D-4 null is BLOCKED (section 0).

**Proposed (strictest):** **do not register now.**
- Register at most **2** hypotheses: the best DIA pair and AVA/PUNDIX, so the family is not dominated by one coin.
- Do so only once (a) OPEN-36 is signed, (b) the D-4 calibration has passed, and (c) the owner has written an
  economic rationale for each.
- Until then the pairs stay EXPLORATORY (ladder level OBSERVATION, never claimable).

**Owner decision:** [ ] defer as proposed [ ] register now: ______ (with rationale attached) [ ] reject the screen
nominations

---

*Context run today (descriptive, zone S, not a decision):*
- **Run:** journal run `BT-d1f6aac01934e215acc5e2d18921e128`, BTC/USDT, 8,000 candles to 2024-03-31: 6,041
  decisions, 287 simulated trades, 18,418 labels.
- **Discovery:** statsmodels logistic over 287 episodes in 4 purged folds. Out-of-fold log-loss 0.600 vs base rate
  0.565 (worse than the base rate), so **0 candidates**. The regime features showed no out-of-fold predictive
  pattern for the hypothetical stop/target outcome.

Signed: ______________________ Date: __________

---

## OWNER SIGN-OFF (2026-10-07, given in the Claude Code session; recorded verbatim in intent)

- **OPEN-36: ACCEPTED** — δ = 1.0 percentage point, α = 0.05, target power 0.80. Size criterion: nominal 5 %,
  one-sided Wilson upper bound ≤ 6 % at 95 % confidence, 3,202 null replicates per scenario. Do not loosen δ, α,
  confidence, power or replicate count for cost or after a failure; report failures honestly; never replace the
  Wilson bound with an observed-rate comparison. Preserve the D-2 calibration as historical evidence; the new
  3,202-replicate calibration is the current attempt. Engle-Granger is not promoted until its own gate passes.
- **D-3: DEFERRED.** The five pairs remain OBSERVATION / EXPLORATORY / NON-CLAIMABLE; no registry entry, no verdict.
  If D-4 calibration passes, prepare a new owner-review package (never automatic registration); the economic
  rationale must be approved by the owner.
- **Sequence:** lock OPEN-36 → run the D-4 Step-17 null calibration → evaluate against the signed criteria → stop on
  failure without weakening → on pass, prepare the D-3 review package.
- **BTC logistic result:** descriptive only, 0 ML candidates; no added ML complexity; no ML output affects
  strategy, risk, execution or live behaviour.

Signed: Steven Bwenje (owner) — 2026-10-07
