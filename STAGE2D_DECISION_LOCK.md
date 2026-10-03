# Stage 2D Rev B — Decision Lock (before Steps 9–20)

## Resolution of 2026-10-02 (owner) — supersedes the open items below where they conflict

**Methodology version** is now `STAGE2D-REVB-2`. It was bumped because the trade lifecycle and the seed semantics changed. A REVB-1 seed or result can never validate as REVB-2.

| ID | Status | Lock |
|---|---|---|
| **C1** entry sign | **LOCKED** | The sign check uses the held spread **at the decision cutoff t**, h_t = ln P_A,t − β(t\|t) ln P_B,t − α(t\|t). That is the only value known when the decision is taken: the fill price is unknown at t. LONG requires h_t < 0, SHORT requires h_t > 0, and h_t = 0 is invalid. Otherwise **no trade**, and an `ENTRY_SIGN_MISMATCH` record is kept (decision timestamp, direction, z, h_t, α_e, β_e, decision-candle closes, reason). It is counted apart from no-signal candles and written to `<PAIR>_entry_sign_mismatches.csv` even when no trade exists. It is a validity condition, not a filter tuned on outcomes. |
| **C2** first exit check | **LOCKED** | Information-availability rule: the entry fills at close(E). The first new close after that is E + 1. So exit decisions are taken at cutoffs u ≥ E + 1 only, the time-stop decision is at E + H − 1, and its fill is at E + H. **Symmetric rule (derived):** an exit-fill close is not also an entry-decision close, so the next entry decision is at the exit fill + 1 or later. |
| **C3** missing exit candle | **LOCKED** (recording); exclusion **documented** | The exit fills at the first observed candle after u + d. The trade carries `exit_fill_candle_missing`, `missing_exit_fill_timestamps`, `holding_candles`, `h_compliant = False` and `data_complete = False`. A trade still open when the data ends is closed as `DATA_END_CENSORED` with `data_complete = False`. See §C3 below. |
| **C4** 2026-09-20 registry | **LOCKED** | A separate legacy experiment. See §C4 below. |
| **C5** live missed candles | **LOCKED** (minimum fail-closed) | A live polling gap means live cannot tell an exchange gap from a missed poll. The engine sets `requires_reconciliation`, records the gap timestamps and blocks **new entries**. It steps the gap as prediction-only grid candles and never invents a price. **Exits stay allowed**: they use only the frozen α_e and β_e, the observed prices and wall-clock H, so they don't depend on the possibly-stale filter state, and closing reduces risk. Backfill/reconciliation itself is deferred. A restart replays from the seed using exchange history, which is authoritative. `confirm_gap_reconciled()` exists for that future reconciler and nothing calls it. |
| **C6** Kalman seed | **Mechanism LOCKED; L = 1000 LOCKED (REVB-3)** | See §C6 below. *Supersession S-1 (2026-10-03): this row read "L BLOCKED" until L = 1000 was locked under STAGE2D-REVB-3. See the Supersession record at the end of this file.* |
| FX | **BLOCKED** | Unchanged. The crypto pipeline proceeds. No FX result may be read as negative evidence. The heuristic `likely_weekend` labels are not a calendar. |

### C3 — Could incomplete trades contaminate later stages?

They can, and they currently do in one place:

| Consumer | Contaminated today? | Required exclusion rule (to be implemented in Steps 13–17) |
|---|---|---|
| OOS labels (H-horizon outcomes) | not yet built | A trade with `data_complete = False` is **not** an H-label and is excluded from every H-conditioned label set |
| Holding-period statistics | yes, if computed over all trades | Computed over `h_compliant` trades only; incomplete trades are counted and reported separately |
| P&L / equity / drawdown | — | **Included**: the cash flow is real (the fill did happen at the later price). Excluding it would misstate realised P&L. The trade stays flagged. |
| Win rate, mean return, Sharpe, profit factor (`_build_strategy_summary`) | **yes**: computed over all trades | The inference/statistic set is restricted to `data_complete` trades. The summary now reports `trades_exit_fill_missing` and `trades_data_incomplete`, so the size of the issue is visible. |
| Permutation inputs | not yet (permutation still runs the LEGACY rule set) | `data_complete` trades only, and decisions must be label-complete (t ≤ Z_e − 25) |
| Walk-forward fold metrics | not yet (LEGACY rule set) | Same as permutation |

No statistical correction is invented here. The rule is exclusion from H-conditioned inference plus separate reporting.

### C4 — The 2026-09-20 registry

`research_data/frozen_candidate_registry_2026-09-20.json` is **preserved unchanged**.

**What it is:**
- Methodology: the pre-Stage-2D rules. That means Kelly sizing; target, z-stop and time-stop exits; partial exits; θ₀ = the first-10,000-candle OLS; and the legacy z definitions. No `METHODOLOGY_VERSION` existed then. Its own `git_commit` field identifies the code.
- Exposure: 108 candles after its freeze (2026-09-20 12:00 → 2026-09-24 23:00) are on disk, and research was re-run after they were downloaded (2026-09-28).

**Status:** a legacy experiment. Not Stage 2D evidence, not an untouched holdout, never a source of Stage 2D parameters or thresholds.

**Ingestion paths checked:** no Python module reads the registry, so nothing can currently ingest it as Stage 2D evidence. **Naming hazard (reported, not changed):** `bot/research/cointegration_engine.py` (lines ~334, 754, 1057) calls its post-training-window ADF test an "untouched holdout" and emits `oos_adf_pvalue` / `passes_oos_persistence`. Under this lock those are historical pseudo-holdout diagnostics and must not be cited as holdout evidence.

### C6 — Causal Kalman initialisation (replaces "seed = selection-zone start, θ₀ = OLS on S")

The prohibited design is: fit β/α on selection candles, then assign the result to the first selection candle. It is **removed everywhere it existed:**
- `KalmanFilterEngine.run()` no longer writes a seed record. Its θ₀ is the 10,000-candle OLS assigned to candle 0, which is exactly this look-ahead.
- Live no longer compares seeds with `cointegration_pairs.csv`'s `hedge_ratio`/`intercept`.

**Canonical design (implemented):**

    [ I_s ............ I_e ] [ seed = I_e + 1h ... warmup ... z-min ... ] [ selection verdicts ... ]
      init window: L grid     filter starts here; 168 warmup candles,       first candle at which any
      candles; θ₀ = OLS of    then 168 valid z values (≥ 336 candles)       selection/performance
      ln P_A on [ln P_B, 1]   with no signal and no evaluation              statistic may be computed
      over THESE candles only

- θ₀ depends only on candles with open time ≤ I_e. Leakage tests:
  - θ₀ is bit-identical when every candle from the seed onward is changed;
  - θ₀ changes when a window candle changes (positive control);
  - the prohibited design is demonstrably detected.
- The seed candle must be observed on both legs. Otherwise there is no canonical seed and the run fails closed.
- **Data roles stay distinct:** initialisation data (the init window), then the selection warmup (seed to seed + 336), then selection, validation, walk-forward and holdout. No later-zone candle can flow into a seed, because the seed record stores the window and live re-derives θ₀ from the exchange's own init-window candles. A mismatch is refused as a data-version mismatch.

> **Superseded (S-1, 2026-10-03).** The paragraph below is the original text, kept unchanged for the record. L is no longer blocked: it was locked at **L = 1000** (Design A, init window before the seed) under methodology `STAGE2D-REVB-3`. Sources: `bot/quant_core/protocol.py` (`init_window_candles = 1000`), `STAGE2D_CANONICAL_PROTOCOL.md` §C6, `STAGE2D_D1_D5_G1_IMPLEMENTATION_REPORT.md` §10.

**BLOCKED — L, the init-window length.** It is a new quantity that no frozen value determines. The 168-candle filter warmup and the 168-value z minimum are frozen, but they govern what happens *after* the seed, not how many candles θ₀ is fitted on. Any value chosen now would be an arbitrary choice that affects every θ₀. `CanonicalProtocol.init_window_candles = None` makes every canonical seeding path raise `ProtocolValueMissing`, so live keeps refusing entries.

**Decision required:** L (in candles), chosen without reference to performance, and whether the init window is the first L candles of the selection zone or a separate initialisation zone before it.

Tests use a labelled **test-only** L = 168, which is not a protocol value.

---

**Methodology version:** `STAGE2D-REVB-1`. The companion specification is `STAGE2D_CANONICAL_PROTOCOL.md`.

**Written:** 2026-10-02, read-only. No code, test, schema or configuration was changed to produce this document.

**Status words:**
- **LOCKED** — frozen by the owner and fully specified here.
- **LOCKED WITH CONDITION** — frozen, but one sub-rule needs explicit confirmation (marked ⚠).
- **BLOCKED** — cannot be frozen from repository evidence. The missing information is listed.

> **Governance rule.** H = 24, g_max = 3, fixed-fraction sizing (f = 0.05), every zone length, every exit threshold and the Kalman Q/R baseline are **protocol assumptions**. None of them is evidence of an edge. None was chosen by looking at profitability, and none may be reported as "discovered".

---

## Notation (shared by every decision)

| Symbol | Meaning |
|---|---|
| k | An hourly UTC grid candle, indexed by its open time o_k. Its close is c_k = o_k + 1 h. Every count below is in **grid** candles (wall-clock hours), not in observed candles. |
| t | The signal information cutoff. The decision uses only data with close ≤ c_t. |
| d | Execution delay, d = 1. The entry fill is at the **close of candle t + d**. |
| e | Entry candle (the fill candle), e = t + d. |
| u | An exit decision cutoff. The exit fills at the close of u + d. |
| β_e, α_e | The posterior θ(t\|t) at the decision cutoff t. Frozen for the life of the trade. |
| W | z window, 504 candles. The minimum number of valid z values is 168, and the warmup after any (re)seed is 168. |

---

## Decision 1 — Held-spread exit sign

**Status: LOCKED WITH CONDITION** (⚠ D1-a, ⚠ D1-b).

### 1.1 Definition

    h_k = ln P_A,k − β_e · ln P_B,k − α_e        for every candle k ≥ t of the trade

α_e and β_e are frozen at the decision cutoff t. The Kalman state may keep updating, or even re-seed, during the trade; h never uses it.

### 1.2 Rule

| Direction | z at the cutoff | Legs | Normalisation exit (decision at u) |
|---|---|---|---|
| LONG_SPREAD | z_t ≤ −2.0 | long A, short β_e·(A-notional) of B | **h_u ≥ 0** |
| SHORT_SPREAD | z_t ≥ +2.0 | short A, long B | **h_u ≤ 0** |

The time stop (Decision 2) applies alongside this rule. Whichever fires first wins.

There is no partial exit, no target-z exit and no z stop-loss.

### 1.3 Why the sign follows from the direction

**Leg sizes.** The legs are N_A = T/(1+β_e) and N_B = β_e·N_A, so N_B/N_A = β_e.

**First-order P&L.** For small moves, a LONG_SPREAD position changes value by

    ΔV ≈ N_A·Δln P_A − N_B·Δln P_B = N_A·(Δln P_A − β_e·Δln P_B) = N_A·Δh

**What that implies:**
- A LONG profits when h **rises**. It is opened when the spread is below its equilibrium (h < 0). Reversion to equilibrium means h climbs to 0, so the LONG exit is **h ≥ 0**.
- A SHORT has ΔV ≈ −N_A·Δh. It profits when h falls and is opened with h > 0, so its exit is **h ≤ 0**.

**Why the wording in the Rev B prompt was reversed.** Exiting a LONG when h ≤ 0 would fire immediately, because a LONG is opened with h < 0.

**Why entry and h usually agree in sign.** At the decision candle,

    h_t = e_t · R / S_t       (the filtered residual)

so h_t has the sign of the innovation e_t, and z_t is a standardised e_t.

### 1.4 ⚠ D1-a — Entry sign condition (not guaranteed by the mathematics)

z_t = (e_t − mean_W)/sd_W. If the rolling innovation mean is far from 0, then z_t ≤ −2 does **not** imply e_t < 0. Example: mean_W = 3·sd_W with e_t = +0.5·sd_W gives z_t = −2.5, but h_t > 0.

A LONG opened with h_t ≥ 0 would satisfy its exit at the first check.

**Proposed lock (strictest, fail-closed):** the entry also requires sign consistency:
- LONG requires h_t < 0;
- SHORT requires h_t > 0.

Otherwise the entry is SKIPPED with reason `ENTRY_SIGN_INCONSISTENT` and counted. This adds a rule, so it **needs owner confirmation**.

### 1.5 ⚠ D1-b — First cutoff at which exits are evaluated

The time-stop equation in Decision 2 implies that exit decisions are taken at every cutoff u with **e ≤ u ≤ e + H − 1**. The entry-fill candle itself is the first exit cutoff. This is causal: the close of e is known at c_e, the same instant the entry is filled.

The current research engine (`entry_exit_engine._scan_candles`) starts exit decisions at e + 1 (it `continue`s after the entry fill). Step 9 must align it to u ≥ e. **Confirm.**

### 1.6 Synthetic examples

Parameters: β_e = 1.3, α_e = 0.2, T = 1000 USDT, so N_A = 434.78 and N_B = 565.22. Values were computed with the formula in §1.1.

**LONG_SPREAD** (entered at z ≤ −2):

| Candle | P_B | P_A | h | Action |
|---|---|---|---|---|
| t (decision) | 10.000000 | 23.649941 | −0.030000 | enter LONG (h < 0 ✓) |
| e + 3 | 10.100000 | 24.392996 | −0.012000 | hold (h < 0) |
| e + 7 = u | 10.200000 | 25.105929 | +0.004000 | **exit decision** (h ≥ 0); fill at close(u + 1) |

Mark P&L from the t-prices to the u-prices: N_A·(P_A ratio − 1) − N_B·(P_B ratio − 1) = **+15.46**. The first-order approximation N_A·Δh = 434.78 × 0.034 = +14.78, with the same sign.

**SHORT_SPREAD** (entered at z ≥ +2):

| Candle | P_B | P_A | h | Action |
|---|---|---|---|---|
| t | 10.000000 | 25.112372 | +0.030000 | enter SHORT (h > 0 ✓) |
| e + 2 | 9.900000 | 24.319906 | +0.011000 | hold |
| e + 5 = u | 9.950000 | 24.163526 | −0.002000 | **exit decision** (h ≤ 0) |

Mark P&L is −N_A·(P_A ratio − 1) + N_B·(P_B ratio − 1) = **+13.60**. The approximation is +13.91.

Profit is not guaranteed in general. The P&L is realised at the fill price, close(u + 1), after costs. h ≥ 0 can be reached through a loss path when β_e is misestimated, because the true and frozen hedge ratios then differ. The examples only show that the sign convention is internally consistent.

---

## Decision 2 — Holding horizon H

**Status: LOCKED** with H = **24** candles as the initial canonical baseline. It is not optimised and is not evidence of anything.

### 2.1 Timing (all in grid candles)

| Event | Candle | Timestamp |
|---|---|---|
| Signal information cutoff | t | close c_t = o_t + 1 h |
| Entry fill | e = t + d = t + 1 | close of e = o_t + 2 h |
| Normalisation-exit decisions | u ∈ [e, e + H − 1] | c_u |
| Time-stop decision (if not exited earlier) | u_TS = e + H − 1 = t + 24 | c_{t+24} = o_t + 25 h |
| Time-stop fill | e + H = t + 25 | close of t + 25 = o_t + 26 h |
| Holding duration (entry fill to exit fill) | ≤ H = 24 h | normalisation exit at u gives u + 1 − e candles, between 1 and 24 |

H is a **wall-clock** quantity. Missing candles inside a trade count toward H.

**Missing exit-fill candle.** If candle u + d is missing, the exit fills at the first observed candle after it. This is the current research rule: an exit is never skipped. The realised holding period then exceeds H. The trade must carry `holding_exceeded_H = True` and the realised duration, and nothing may silently treat it as ≤ H. **Locked as a recording rule.**

**State re-seed during a trade.** h uses the frozen α_e and β_e and the time stop is wall-clock, so neither depends on the filter state. An open trade is **not** force-closed by a re-seed. New entries remain suspended until warmup completes. **Locked.**

### 2.2 Dependency ranges of one trade decided at t

| Quantity | Prices used (candle range) |
|---|---|
| z_t (rolling window) | [t − W + 1, t] = [t − 503, t] |
| Kalman state θ(t\|t) | [seed, t]. Recursive, so it carries all history since the seed with decaying weight |
| Warmup / z availability | at least 168 + 168 = 336 candles after the seed before the first possible signal |
| Outcome (trade P&L / OOS label) | [e, e + H] = [t + 1, t + 25], i.e. d + H = 25 candles ahead of the cutoff |

### 2.3 Relationship to the OOS labels

The canonical label for a decision at t is the trade outcome. It depends on prices up to t + d + H at the latest. A decision is **label-complete** inside a zone Z = [Z_s, Z_e] only if t + d + H ≤ Z_e, that is t ≤ Z_e − 25.

**Lock:** decisions in the last d + H = 25 candles of a zone are excluded from that zone's evaluation (label truncation). They are never completed with prices from the next zone.

### 2.4 Purge (derived)

**What is fitted on earlier data.** Only the selection-stage objects: the EG test, the OLS seed θ₀, and pair inclusion. Q, R, the thresholds, H and f are frozen and never fitted.

**Overlap condition.** Let an earlier ("training") zone end at T₀ − 1 and the evaluation zone start at T₀.
- A training-side decision t′ has label window [t′ + d, t′ + d + H].
- The first evaluation label starts at T₀ + d.
- They overlap iff t′ + d + H ≥ T₀ + d, which is iff **t′ ≥ T₀ − H**.

**Purge rule:** remove training-side decisions t′ ∈ [T₀ − H, T₀ − 1]. That is **H = 24 candles**.

This is equivalent to the label truncation in §2.3, applied to the earlier zone. Truncation at d + H = 25 is the stricter of the two and is the one locked. It also removes the decision whose entry fill would sit at T₀.

**Selection statistics.** The EG test and the OLS fit use **prices**, not labels. For them the requirement is only that every selection price has close ≤ c_{S_e}. An evaluation decision t ≥ T₀ = S_e + 1 has its label starting at t + 1 > S_e, so there is no price overlap.

### 2.5 Embargo (derived, not copied)

An embargo protects observations that come **after** an evaluation zone and are used for fitting or selection. Two separate dependency channels apply.

**(i) Label channel.**
- An evaluation zone [T₀, T₁] has labels reaching T₁ + d + H.
- A later fitting-side decision t″ has label window [t″ + d, t″ + d + H].
- That window overlaps an evaluation label iff t″ ≤ T₁ + H.
- So the label embargo is **H = 24 candles** after T₁.

**(ii) Feature channel (z window).**
- The z of a later decision t″ uses prices from t″ − W + 1 onwards.
- It contains no evaluation-period price (the last one is at T₁ + d + H) iff t″ − W + 1 > T₁ + d + H.
- That is iff t″ ≥ T₁ + d + H + W.
- So the feature embargo is **d + H + W − 1 = 1 + 24 + 503 = 528 candles**.

**(iii) Kalman state.** θ(t\|t) depends on every price since the seed. No finite embargo removes that dependence; only a re-seed does. A re-seed costs 336 candles of burn-in and breaks D9 (one continuous canonical state). **Locked consequence:** the design must not fit anything on data **after** an evaluation zone. All zones are strictly chronological, and walk-forward is anchored or expanding with fitting always before evaluation. Under that design the post-zone embargo (i)–(ii) is never exercised, and only purge plus label truncation apply.

**(iv) Static (OLS) spread normalisation at the selection → validation boundary.**
- The static spread's residuals inside S were fitted in-sample, so their variance is shrunk.
- A validation z at t uses residuals from [t − 503, t].
- Those residuals contain no in-sample-fitted residual iff t − 503 > S_e, that is t ≥ S_e + 504.
- **Lock:** for static-spread features, the first validation decision is at **S_e + W = S_e + 504**, an embargo of W − 1 = 503 candles after S_e.
- The Kalman spread uses one-step-ahead innovations, which are not fitted in-sample, so it needs no such embargo. Static vs Kalman comparisons must use **the same** first decision candle (S_e + 504) so that the comparison stays fair.

**Summary of gaps (with d = 1, H = 24, W = 504):**

| Boundary | Gap |
|---|---|
| Zone end, label truncation (every zone) | 25 candles |
| Purge before any evaluation start | 24 candles (covered by the 25-candle truncation) |
| S → V for static-spread features | 503 candles (applied to both methods for fairness) |
| Post-evaluation embargo | not applicable: nothing is fitted after an evaluation zone. If a future design does so, it needs at least 528 candles **and** a re-seed |
| Forward holdout start F | first eligible decision t ≥ F (features may use prices before F; labels must lie after F, which they do automatically since e = t + 1 > F) |

### 2.6 Walk-forward purging

Each fold k has a fitting span [A, B_k] (anchored or expanding) and an evaluation span [B_k + 1, B_{k+1}].
- Decisions in the last 25 candles of each fitting span are excluded from fitting.
- Decisions in the last 25 candles of each evaluation span are excluded from fold k's metrics; they are not added to fold k + 1.
- The Kalman filter runs continuously from the canonical seed across all folds. There is no per-fold re-seed and no per-fold burn-in.

The fold metadata must record, for every fold: A, B_k, B_{k+1}, d, H, W, the purge and truncation counts, and the number of decisions removed.

### 2.7 Relationship to permutation testing

The timing null rotates or permutes decision times. Every permuted decision must satisfy the same label-completeness rule (t ≤ Z_e − 25), and the same entry sign condition if D1-a is confirmed. The block structure must also respect the 25-candle label length, so that overlapping outcomes are not treated as independent.

---

## Decision 3 — Research zones

**Status: ZONE DECISION = BLOCKED.** Calendar boundaries cannot be established from repository evidence. Inventing them would be a hidden assumption.

### 3.1 Evidence available

**Data on disk (pilot symbols, read-only check):**

| Item | Value |
|---|---|
| `data/{DODO,MINA,ONG,AVAX,ATOM}_USDT_1h.csv` | 2021-09-26 00:00 → 2026-09-24 23:00 UTC. 43,800 grid candles, 43,797 observed, 0 duplicates, maximum gap 3 h (no gap > g_max) |
| `data/FIDA_USDT_1h.csv` | 2021-09-30 12:00 → 2026-09-24 23:00. 43,692 grid candles, 43,691 observed |
| DODO/FIDA common span | 43,692 grid candles |
| Files last written | 2026-09-24 ~23:00–23:32 |
| The 150-symbol universe (11,175 pairs) | listing dates and common spans **not inspected**; they vary per symbol |

**Exposure history:**

| Item | Value |
|---|---|
| Legacy cointegration training window | the first 10,000 candles of each pair, i.e. 2021-09-26 00:00 → 2022-11-16 15:00 for symbols starting 2021-09-26. The ADF "holdout" test then used **all remaining candles**, and the owner has ruled that this legacy split is not to be reused as the selection zone |
| Walk-forward | 1-year test windows across the whole history (`TEST_WINDOW_YEARS = 1`) |
| Repeated scans | 100-coin scan, 19-symbol rescan, 3 pairs appended 2026-09-28; `cointegration_pairs.csv` written 2026-09-28 |
| Frozen candidate registry | `research_data/frozen_candidate_registry_2026-09-20.json`: freeze timestamp 2026-09-20T11:58:08Z, 31 pairs, under the **pre-Stage-2D rules** (Kelly sizing, target and z-stop exits, legacy z definitions). Its own text records that *"no genuinely untouched historical holdout"* exists anywhere in the data chronology |
| Data after that freeze already on disk | 2026-09-20 12:00 → 2026-09-24 23:00, **108 candles**. The 2026-09-28 cointegration rewrite ran after these were downloaded, so they must be treated as exposed |
| Live dry-run pilot | has processed live candles since its start; those candles have been seen through logs and dashboards |

### 3.2 A. Historical pseudo-holdout vs B. genuine forward holdout

| | Available? | Eligible for final holdout claims? |
|---|---|---|
| **A. Historical pseudo-holdout** (any split of 2021-09-26 → 2026-09-24) | Yes, as a mechanical split only | **No.** Every historical candle has been exposed to selection, parameter, exit-rule or design decisions across many sessions. A historical split can serve as a development check and must be labelled `PSEUDO_HOLDOUT — NOT CLAIM-ELIGIBLE` |
| **B. Genuine forward holdout** | **Not yet.** It begins only at the Stage 2D Rev B protocol-freeze timestamp F, once Steps 9–20 are implemented, the methodology version is frozen and the candidate set is registered | **Yes**, for candles with open time ≥ F, evaluated once, with no re-fitting |

**The 2026-09-20 registry is not this protocol's holdout.** It is a separate, pre-registered experiment on different rules, and its first 108 post-freeze candles are exposed. It must not be reused to evaluate STAGE2D-REVB rules.

### 3.3 Zone proposal (structure only; every boundary pending)

| Zone | Start | End | Hourly observations / complete candles | Previously exposed? | Claim-eligible? |
|---|---|---|---|---|---|
| Selection S | **BLOCKED** | **BLOCKED** | n/a | yes, all history | no |
| Validation V | **BLOCKED** (≥ S_e + 504) | **BLOCKED** | n/a | yes | no |
| Walk-forward folds | **BLOCKED** (inside S ∪ V, chronological) | **BLOCKED** | n/a | yes | no (development evidence only) |
| Sealed forward holdout | F (not yet set; strictly after the Rev B freeze) | F + L_H (L_H not set) | n/a | no, if sealed from F | **yes** |

### 3.4 Information required to unblock

1. **Owner choice** of the S, V and walk-forward split rule. That is either calendar boundaries, or fractions of each pair's common span with a stated rounding rule. Lengths must be chosen without reference to performance.
2. **The universe list** (the 150 symbols) and each pair's common observed span, so the zones can be computed per pair or on a common calendar (owner choice).
3. **The protocol-freeze timestamp F.** It must come after Steps 9–20 are implemented and tested and the methodology version and candidate registry are frozen. A real calendar time cannot be set in advance.
4. **The forward-holdout length L_H and the evaluation rule.** These need the effect size to detect, the expected trades per pair per month under H = 24 (unknown until Step 9 runs on development data), and the error rates. If they are chosen without these, the low power must be stated in advance.
5. **The relationship to the 2026-09-20 registry experiment**: keep it running separately, or retire it. It must not be merged into the Rev B holdout.

---

## Decision 4 — Crypto gap policy

**Status: LOCKED** (already implemented in Steps 3–6).

g_max = 3 grid candles. For g consecutive missing grid candles:

| Case | Rule |
|---|---|
| 1 ≤ g ≤ 3 | Keep the grid. Each missing candle is a prediction-only step: θ unchanged, P ← P + Q, so P(t+g\|t) = P(t\|t) + g·Q. z gets a NaN slot. |
| g > 3 | The state is invalid from the 4th missing candle. At the next observed candle: re-seed with the canonical θ₀ and P₀ = I, start a new segment, clear the z history, restart the 168-candle warmup. No signal until warmup **and** the 168-valid-z minimum are met, i.e. at least 336 candles. |

### Research/live identity: verified

**Same code path.** Both research and live call `bot/quant_core/kalman.CanonicalKalman` and `CanonicalZScore`. Research reaches them through `KalmanFilterEngine._run_filter` over the complete hourly grid from `_align_prices`. Live reaches them through `PairSignalEngine._advance_state`, which steps the missing grid candles using timestamps.

**Tests:**
- `test_quant_core_kalman.GapPolicyTests`: g = 1, 2, 3 match P + gQ to 1e-15; g = 4 re-seeds; a re-seed equals a fresh filter.
- `test_parity_forensics.DatasetBGaps`: a 3-hour gap gives identical state on both sides; a 5-hour gap re-seeds both identically, with segment 1, warmup and z NaN for 168 + 167 candles.
- `test_canonical_seed.LiveCandleSequenceTests`.
- **Data check:** no pilot-symbol file has a gap > 3, so the re-seed branch has not been triggered on real data.

**Remaining limit (Step 11, not a policy change).** Identity holds for candles **absent from the data source**. A candle that exists on the exchange but that the live loop *missed* (for example a failed poll) is currently treated by live as missing, while research observes it. The owner's D7 "backfill skipped candles" is Step 11 and is **not implemented yet**. Until it is, live-only skips are a known parity break. They are measured by `test_candle_skipped_by_live_only_is_detected`.

---

## Decision 5 — FX market-closure policy

**Status: BLOCKED (dependency missing).**

**Separation (locked as a principle):**

| Event | Treatment |
|---|---|
| A. Scheduled market closure | Not missing data. Grid candles inside a scheduled closure would be **excluded from the grid**: no prediction step, no Q accumulation, no warmup count, no z slot. The crypto > 3 re-seed rule must not fire on them. |
| B. Unexpected missing data during market hours | An explicit FX data-gap policy. Its g_max is an owner value, not assumed equal to the crypto value. |

**What the repository has:**
- No market-calendar library is installed: no `pandas_market_calendars`, `exchange_calendars` or `holidays` in `venv`.
- No authoritative calendar module exists in the code.
- `bot/forex_data_fetcher.py:204-225` and `bot/mt5_data_fetcher.py` label gaps `likely_weekend` when the gap starts on a Friday or Saturday. The code calls this *"an honest heuristic label, not a claim of a modeled calendar"*.
- That heuristic cannot distinguish holidays (25 Dec, 1 Jan), early closes, broker-specific session times (MT5 server time zone) or a genuine Friday outage.

**Dependency to unblock:** an authoritative FX session calendar. Either:
- a vetted library added to the environment; or
- an owner-specified rule naming the source (for example: "Sunday 22:00 UTC – Friday 22:00 UTC, with DST-dependent times per venue X, holidays per list Y"),

plus the FX unexpected-gap g_max.

**Until then:**
- FX Kalman pairs remain under the crypto rule in code. That makes them inert, because the ~48 h weekly gap re-seeds them every week (see the protocol §8 item 4).
- FX results from this engine must be reported as `FX_CALENDAR_UNAVAILABLE`, not as negative evidence.
- Weekend timestamps are not guessed.

---

## Dependency table

✔ = depends on the decision. ◐ = depends on part of it (stated).

| Component | D1 exit sign | D2 H | D3 zones | D4 crypto gaps | D5 FX calendar |
|---|---|---|---|---|---|
| Entry/exit engine (Step 9–10) | ✔ exit rule, entry sign (D1-a), first exit cutoff (D1-b) | ✔ time stop at e + H − 1, holding flag | — | ✔ held trade survives a re-seed; wall-clock H | ✔ FX only |
| OOS target construction | ✔ label sign σ and h-based exit path | ✔ label horizon d + H; 25-candle truncation | ✔ zone ends | ✔ wall-clock horizon over gaps | ✔ FX horizon over closures |
| Purge | — | ✔ 24 (covered by 25) | ✔ boundaries | — | ◐ whether closure hours count in H |
| Embargo | — | ✔ 528 / 503 derivation | ✔ boundaries | ◐ a re-seed is the only Kalman isolation | ◐ |
| Walk-forward | ✔ via trades | ✔ fold truncation | ✔ fold spans | ✔ continuous state across folds | ✔ FX |
| Permutation testing | ✔ statistic depends on exits | ✔ block length ≥ 25 and label completeness | ✔ the family lives inside one zone | ◐ permuted times must avoid warmup and invalid segments | ✔ FX |
| Pair selection (EG on S) | — | — | ✔ S | ✔ the EG residual on the grid (gap handling) | ✔ FX |
| Seed records (D9) | — | — | ✔ the seed candle should be the S start, not "first data candle" (see contradiction C6) | ✔ re-seed semantics | ✔ FX |
| Live/research parity | ✔ | ✔ | — | ✔ (Step 11 backfill) | ✔ |
| Holdout | ✔ frozen rule | ✔ frozen H | ✔ F and L_H | ✔ | ✔ |
| Statistical inference (BH/BY, IC, Sharpe descriptive) | ✔ trade outcomes | ✔ overlap (outcome dependence ≤ 25 candles) | ✔ family per zone | ◐ effective sample size after warmup | ✔ |

---

## Contradictions and gaps found while locking

| ID | Finding | Effect | Needs |
|---|---|---|---|
| C1 | The entry sign is not guaranteed by the z sign (§1.4) | An immediate exit is possible | Owner: confirm D1-a (skip on inconsistent sign) |
| C2 | Research starts exit decisions at e + 1; the H equation implies e (§1.5) | The time stop and normalisation start one candle late in research | Owner: confirm D1-b |
| C3 | A missing exit-fill candle makes the realised hold exceed H (§2.1) | "Maximum H" can be exceeded | Locked as a recording rule; no owner input unless you prefer a different rule |
| C4 | The 2026-09-20 frozen registry is a forward holdout for **old** rules, and 108 post-freeze candles are on disk with research run after them (§3.1) | It cannot serve as the Rev B holdout | A new freeze F after Steps 9–20 |
| C5 | D7 backfill of live-missed candles is not implemented (Decision 4) | Live-only skips diverge | Step 11 (already scheduled) |
| C6 | Research seeds at the first data candle (2021-09-26), but EG selection is to be run on zone S | If S does not start at the data start, the seed θ₀ (OLS on S) is fitted on data the filter has already passed, or after the seed candle | Owner: the canonical seed candle = S_s, and θ₀ = OLS on S; the filter's first evaluated decision is in V. Today θ₀ is the legacy 10,000-candle OLS. It must be re-derived once S is fixed. |
| C7 | The Kalman state carries all past information (§2.5 iii) | No finite embargo isolates fitting done after evaluation | Locked: fitting only before evaluation |

---

## Exact blockers before Steps 9–20

1. **D1-a** — confirm the entry sign-consistency rule (skip if h_t has the wrong sign).
2. **D1-b** — confirm that exit decisions start at the entry-fill candle e (u ≥ e).
3. **Decision 3** — the zone split rule, the universe spans, the freeze timestamp F, and L_H with its evaluation rule. Status: BLOCKED.
4. **C6** — confirm that the canonical seed candle = the selection-zone start and θ₀ = OLS on S. This replaces the legacy first-10,000-candle seed.
5. **Decision 5** — an FX calendar source and the FX unexpected-gap g_max. Without them, FX stays BLOCKED; crypto is not blocked by this.
6. **The 2026-09-20 registry** — keep it as a separate experiment, or retire it.

With 1, 2 and 4 confirmed:
- Steps **9–11** (held-spread exit, time stop, skipped-candle backfill) can be implemented on development data (pseudo-holdout labels only).
- Steps **13–17** (purge/embargo with real boundaries, OOS/WF, statistics, multiple testing, permutation) additionally need item 3.
- No step may touch candles at or after F until the holdout is formally opened.

---

## Supersession record

Entries are appended only; superseded text above stays in place, marked.

| ID | Date | Superseded text | Current value | Authority | Found by |
|---|---|---|---|---|---|
| S-1 | 2026-10-03 | C6 "L BLOCKED"; §C6 "BLOCKED — L, the init-window length"; "`init_window_candles = None`" | **L = 1000**, Design A (`A_PRE_SELECTION`), methodology `STAGE2D-REVB-3` | `bot/quant_core/protocol.py`; `STAGE2D_CANONICAL_PROTOCOL.md` §C6; `STAGE2D_D1_D5_G1_IMPLEMENTATION_REPORT.md` §10 | Five-gate audit F-15 |

**Still BLOCKED (unchanged by S-1):** the zone decision (S / V / WF / F calendar boundaries) and the forward-holdout start. C6's remaining clause, the seed candle = the selection-zone start, depends on zone S and is therefore still open.
