# Stage 2D Rev B — Pre-Step-12 Protocol Audit

**Scope:** init window L, seed placement, legacy/canonical governance, C1, C5.

**Date:** 2026-10-02. **Methodology version:** `STAGE2D-REVB-2`.

**Mode:** read-only. No production code, test, schema or configuration was changed. The experiments are scratch scripts (listed in §13) that use synthetic data and the long-exposed early real history only. No P&L, signal or holdout metric was computed. No exchange was contacted.

---

## 1. Executive verdict

# CONDITIONALLY_READY_FOR_STEP_12

Step 12 in the canonical order is the borrow-unavailable state. It does not depend on L, the zones or the FX calendar. It may start once the conditions below are met:

1. **D-1 fixed.** The approved C5 condition is **not met**: exits during reconciliation are not marked `RECONCILIATION_AFFECTED`.
2. **D-4 fixed.** The governance invariant is **violated**: a legacy-seeded Kalman file runs through the canonical engine and comes out labelled `STAGE2D-REVB-2` with the canonical protocol hash (demonstrated, §6).
3. **D-2 fixed.** C1 mismatch events in live are in memory only, and the runner's log line hides their reason.

These are blockers for Steps 13–20 and for any canonical evidence, but not for Step 12:
- **L is not empirically derivable.** An owner baseline is required (§2–3).
- **D-3:** live entry eligibility is decided by legacy verdicts.
- **D-5:** no canonical data version exists.
- The zones and the FX calendar (unchanged from the decision lock).

**L verdict: L CANNOT BE EMPIRICALLY DERIVED FROM CURRENT EVIDENCE.**

---

## 2. L derivation analysis

| Candidate source | Value | Mathematically required? | Statistically required? | Merely historical convention? | Appropriate for L? |
|---|---|---|---|---|---|
| OLS identifiability of [β, α] | ≥ 2 observations with var(ln P_B) > 0 | **yes** (a minimum) | — | no | Sets a floor only. Already enforced in `ols_theta`. Does not determine L. |
| OLS residual variance | ≥ 3 | yes, for a residual variance (θ₀ needs none) | — | no | No |
| Kalman P₀ = I | — | no. θ₀'s influence decays under the data; nothing requires a particular amount of data before the seed | no | no | No |
| Filter warmup | 168 | no; it acts **after** the seed | no | baseline value | **No** (explicitly excluded) |
| z window / z minimum | 504 / 168 | no; they act after the seed, on innovations after the seed only | no | baseline values | **No** (explicitly excluded) |
| Cointegration `MIN_CANDLES` | 1,000 | no | loosely: the minimum sample for a selection test | partly convention | No. It constrains the **selection** sample (EG on S), not θ₀ |
| Legacy `TRAINING_WINDOW` | 10,000 | no | no | yes. It is also the look-ahead seed source | **No** (prohibited design) |
| Legacy live `BOOTSTRAP_CANDLES` | 1,000 | no | no | yes (Binance kline page size) | **No** |
| Spread half-life (OLS on a window ≪ several half-lives is unreliable) | pair-specific | no | **yes in principle** (§3 A: RMSE grows with half-life) | no | **Not usable.** The half-life is estimated on selection data, which comes *after* the init window. Using it would be circular, or would leak back into the seed |
| Feature lookbacks (z, warmup) | 168–504 | no | no | baselines | No (they act after the seed) |
| Research/live parity | any L | no | no | — | Any L gives exact parity through the seed record (tested) |
| Synthetic certification | 168 in tests | no | no | **test-only label** | **No** |

**Conclusion.** The mathematics gives only a lower bound (2). No statistical requirement in the current methodology fixes L without circularity. Every other candidate is a convention or acts after the seed.

---

## 3. Candidate L evidence (stability only; no P&L, IC, Sharpe or win rate)

### A. Known-β recovery and conditioning

Synthetic data, β = 1.3, OLS on the window, 400 reps per cell. Seeds 1000 + half-life.

| Half-life (h) | L | Bias β | RMSE β | RMSE α | Median cond(XᵀX) | Median \|Δβ\| when the first/last 5 observations are dropped |
|---|---|---|---|---|---|---|
| 6 | 168 / 336 / 504 / 1000 | +0.005 / +0.001 / +0.001 / −0.001 | 0.161 / 0.095 / 0.057 / 0.029 | 0.37 / 0.22 / 0.13 / 0.07 | 2.1e4 / 1.1e4 / 6.1e3 / 2.8e3 | 0.015 / 0.005 / 0.003 / 0.001 |
| 48 | same | +0.04 / −0.01 / +0.01 / −0.01 | 0.47 / 0.37 / 0.30 / 0.19 | 1.09 / 0.86 / 0.69 / 0.44 | 2.3e4 / 9.5e3 / 6.4e3 / 3.5e3 | 0.030 / 0.013 / 0.008 / 0.002 |
| 120 | same | +0.01 / −0.05 / +0.02 / +0.01 | 0.55 / 0.50 / 0.45 / 0.34 | 1.27 / 1.14 / 1.04 / 0.79 | 1.9e4 / 1.0e4 / 6.5e3 / 3.7e3 | 0.036 / 0.016 / 0.010 / 0.004 |

### B. Influence of θ₀(L) on the Kalman state after the seed

Median |β_t(θ₀(L)) − β_t(θ_true)|, same post-seed data, canonical Q, R and P₀.

| Half-life | L | k = 1 | 168 | 336 (first possible z) | 1,000 | 3,000 | \|Δz\| at the first z |
|---|---|---|---|---|---|---|---|
| 6 | 168 | 1.0e−1 | 1.8e−2 | 1.1e−2 | 3.2e−3 | 6.2e−4 | 4.1e−3 |
| 6 | 1000 | 2.4e−2 | 3.8e−3 | 2.1e−3 | 6.7e−4 | 1.3e−4 | 1.4e−3 |
| 120 | 168 | 3.8e−1 | 5.5e−2 | 3.1e−2 | 9.9e−3 | 2.1e−3 | 2.0e−2 |
| 120 | 1000 | 1.9e−1 | 3.8e−2 | 2.0e−2 | 6.1e−3 | 1.7e−3 | 7.1e−3 |

### C. Structural break at the window midpoint (β 1.3 → 1.0, half-life 48)

| L | Mean β̂ | sd |
|---|---|---|
| 168 | 1.52 | 4.48 |
| 336 | 0.97 | 3.13 |
| 504 | 1.01 | 2.62 |
| 1000 | 1.16 | 1.88 |

No L estimates either regime reliably. A break inside the init window is not detectable from θ₀ alone.

### D. Early real data (pilot pairs, from each pair's data start in 2021, long exposed)

| Pair | L | β₀ | cond | \|Δβ\| when the first/last 5 are dropped | β₀ range when the window start shifts 0–48 h |
|---|---|---|---|---|---|
| DODO/FIDA | 168 / 336 / 504 / 1000 | 0.788 / 0.529 / 0.736 / 0.540 | 7.9e3 / 7.9e3 / 8.0e3 / 1.0e3 | 0.034 / 0.014 / 0.025 / 0.003 | **0.97** / 0.26 / 0.33 / 0.04 |
| MINA/ONG | same | 0.595 / 0.420 / 0.417 / 0.347 | 259 / 91 / 127 / 207 | 0.010 / 0.003 / 0.004 / 0.003 | 0.05 / 0.04 / 0.07 / 0.02 |

**Influence of L on the state** (measured against L = 1000, from a common seed candle). On DODO/FIDA with L = 168:
- 2.4e−1 at seed + 1;
- 1.7e−2 at seed + 336;
- 4.3e−3 at seed + 1,000;
- **1.7e−5 at seed + 3,000.**

On MINA/ONG the decay is faster: 1.3e−6 at seed + 3,000.

### Reading

- Every stability measure improves **monotonically and smoothly** with L. There is no kink, plateau or threshold. The only counterweight is data cost: L candles of history per symbol before the seed.
- So the choice of L is a value judgment between stability and data cost, not something derivable. **L CANNOT BE EMPIRICALLY DERIVED FROM CURRENT EVIDENCE.**
- On real data, short windows are fragile: DODO/FIDA's β₀ moves by up to 0.97 when the window start shifts by 48 h at L = 168.
- **What matters more than L is the distance from the seed to the first Kalman-based evidence.** θ₀'s influence on the state falls to about 10⁻⁵ to 10⁻⁶ within about 3,000 candles. Under the recommended placement (§4), no Kalman-based evidence is taken in the selection zone. With a selection zone of several thousand candles, L's effect on the first **validation** decision is then of order 10⁻⁵ in β.

### Recommendation (owner decision, not silently taken)

- Freeze L explicitly as a protocol baseline.
- On the stability evidence alone, **L = 1000** is the most stable of the tested candidates on every measure. It is *also* the legacy `MIN_CANDLES` / `BOOTSTRAP_CANDLES` value; that coincidence is **not** the reason for this suggestion.
- **Required companion rule:** any later result must be reported under a sensitivity check. Re-run with **L = 504**, on development zones only, and confirm that no selection or validation conclusion changes.

---

## 4. Recommended seed placement

| | Design A — pre-selection initialisation | Design B — selection-zone warmup |
|---|---|---|
| Layout | [INIT: L candles] → seed = S_s → S, V, WF … | S_s = start of INIT; selection statistics begin after INIT + warmup |
| Leakage | none: θ₀ uses candles before S only | none if the selection statistics exclude INIT; θ₀ is still causal |
| Multiple testing | EG on S never touches the init data; the family is defined on S alone | EG must explicitly **exclude** the INIT candles inside S, or they are used twice |
| Pair selection | unaffected by θ₀ (EG is static and uses no Kalman quantity) | same, *if* the exclusion is enforced |
| Data requirement | each symbol needs L candles before S_s. Late-listed symbols are excluded or shorten L, and that must be recorded **before** the family is formed | none extra, but S shrinks by L + warmup for every pair |
| Reproducibility | seed record = [I_s, I_e], θ₀ | identical |
| Live/research parity | identical (seed record; live re-derives θ₀ from exchange data) | identical |
| Embargo | none needed between INIT and S: θ₀ is fitted strictly before, and the z history starts at the seed. The static-spread S→V embargo (503) is unchanged | the same, plus an internal "no-statistics" boundary that is easy to violate silently |
| Holdout | unaffected; the holdout features depend causally on all history (unavoidable, documented) | unaffected |

**Mathematically required in both designs:** at least 336 candles after the seed before any z can exist (warmup 168 + z minimum 168).

Under A this burn-in sits inside S. That is harmless: no selection statistic uses the Kalman, and **no Kalman-based evidence may be drawn from S at all.** Kalman-based evidence starts at V, by which point θ₀'s influence is about 10⁻⁵ (§3).

**Recommendation: Design A.** It is the only layout where the initialisation observations sit in **no** statistical family by construction, rather than by an exclusion rule that has to be enforced. It is therefore the more defensible under the established principles (strictest option; no hidden reuse).

Cost: L extra candles of listed history per symbol. That changes which symbols are eligible, so the availability filter must run **before** the family is defined and be recorded with the family.

**Neither design can be frozen yet:** both need L (owner) and S_s (zones, BLOCKED).

---

## 5. Selection / initialisation data relationship (Design A)

| Role | Contains the init observations? |
|---|---|
| Selection family (pairs tested) | **No.** Family membership is defined on S availability (after the INIT-availability filter, which is recorded) |
| Statistical sample (EG, half-life, prefilters) | **No.** Computed on S only |
| Pair-testing family (BH/BY m) | **No** |
| Experiment registry | **Yes, as provenance only.** The seed record (window, θ₀, method, protocol hash) is part of the run's lineage |
| Final evidence (V, WF, holdout) | **No.** But see the dependency below |

**Documented dependency (not pretended away).** Every Kalman quantity at any later time is a deterministic function of θ₀, and therefore of the init observations. The influence decays: §3 measures about 10⁻² at seed + 336 and ≤ 2×10⁻⁵ at seed + 3,000. So V, WF and holdout evidence is **conditionally dependent** on the init data, with a measured, decaying magnitude.

The prohibited triple use (init + discovery + validation of the same observations) does not occur under Design A. Under Design B it is prevented only if the EG exclusion of INIT is enforced in code.

---

## 6. Legacy / canonical governance audit

**Invariant:**
- CANONICAL ⇒ canonical methodology version ∧ protocol hash ∧ data version ∧ seed lineage;
- LEGACY ⇒ cannot satisfy canonical evidence.

| Question | Finding |
|---|---|
| Legacy outputs unmistakably labelled? | **Partly.** `EntryExitEngine` summaries carry `rule_set` and `methodology_version` (`LEGACY` vs `STAGE2D-REVB-2`). **Cointegration, permutation and walk-forward outputs carry no methodology tag at all** (`cointegration_pairs.csv`, `permutation_verdict.csv`, walk-forward summaries). Their legacy status is implicit only. |
| Stage 2D outputs carry the correct version? | The version and protocol hash: yes. **Seed lineage and data version: no** (D-4, D-5). |
| Can a legacy result satisfy a canonical verdict reader? | **YES — D-3.** `bot/pairs/config.py::_load_validated_win_rate` decides live **entry eligibility** for the canonical live engine from `cointegration_pairs.csv` `passes_filters` (legacy first-10,000 split) and `permutation_verdict.csv` `edge_appears_real` (legacy rules). `bot/pairs/health.py` gates entries on the same `passes_filters`. Neither checks a methodology version. It is masked today only because no causal seed exists, so live refuses entries anyway. |
| Old permutation result read by a canonical component? | **Yes** (as D-3). |
| Old cointegration result mistaken for Stage 2D evidence? | **Yes** (as D-3). Also the naming hazard noted in the decision lock: `cointegration_engine.py` calls its post-window ADF an "untouched holdout". |
| Old OOS/WF results mixed with Stage 2D outputs? | No canonical component reads walk-forward outputs. The dashboard and research-lab views present them unlabelled (a presentation risk, not a canonical-reader defect). |
| Old seeds validate under REVB-2? | **No.** The old schema loads as `None`, and the methodology and protocol-hash checks run on top (tested in `test_canonical_seed`). |
| Cached files bypass checks? | **YES — D-4.** `EntryExitEngine(rule_set="CANONICAL").run(kalman_csv=…)` accepts **any** Kalman CSV. **Demonstration** (`scratchpad/gov_demo.py`): a CSV produced by the legacy seeding path (config θ₀ at candle 0, with a legacy `is_training_window` span) ran canonically and produced 23 trades, **14 of them inside the legacy look-ahead span**. The summary was labelled `methodology_version = STAGE2D-REVB-2` with the canonical protocol hash, and had no seed or data columns. The raw-data pickle caches (`bot/cache`) are inputs, not results, but with no data version (D-5) a changed cache is undetectable, except at live start through the θ₀ reproduction check. |

**Required for the invariant (not implemented here):**
1. Canonical Kalman output must carry its seed-record identity and a data-version digest.
2. The canonical engine must refuse input without a valid, matching lineage.
3. Canonical summaries must carry the seed lineage and data version.
4. Live eligibility must come from canonical verdicts only. Until those exist, the strict reading is that **no** pair is eligible.

---

## 7. C1 verification

| Check | Result |
|---|---|
| All information exists at decision time | ✔ h_t = ln P_A,t − β(t\|t) ln P_B,t − α(t\|t). The closes of t are known at c_t, and θ(t\|t) is the update by x_t, also known at c_t. Live uses the previous observed candle's closes and requires them to **be** the decision candle (`prev_obs[0] == decision.timestamp`), otherwise `missing_decision_candle`. |
| No fill information leaks back | ✔ Research checks at the decision row, before `pending_entry`. The fill row is not consulted. Live uses `prev_obs`, never the processing candle's prices. |
| LONG/SHORT signs agree with P&L | ✔ Under `pnl.py` sizing, ΔV ≈ σ·N_A·Δh (decision lock §1.3; the numeric example has mark P&L +15.46 / +13.60 against first-order +14.78 / +13.91). |
| A mismatch cannot enter P&L | ✔ Research `continue`s before sizing and `pending_entry`; live returns before a trade object exists (tests: `trades.empty`, `completed_trades == []`, `open_trade is None`). |
| Counted separately | Research: ✔ (`entry_sign_mismatches`, the summary field, a CSV always written). **Live: ✗ partly (D-2).** Live events live only in `PairSignalEngine.entry_sign_mismatches`, in memory, lost on restart and never journaled. The runner's log line prints `decision.direction or decision.reason`, so a mismatch is logged as `action=NONE \| LONG_SPREAD` with the reason hidden. |

**C1 decision logic: causally correct. No defect in the rule.** D-2 is a recording defect.

---

## 8. C5 verification

| Required | Implemented? |
|---|---|
| New entry blocked while flagged | ✔ `reconciliation_required` (tested) |
| Existing position may exit | ✔ The exit path runs before any flag check |
| Exit uses frozen α_e, β_e, observed prices, the canonical rule and H | ✔ `trade.entry_alpha/beta`, `prev_obs` closes, `lifecycle.exit_decision` |
| Exit marked `RECONCILIATION_AFFECTED` | **✗ — D-1.** No such field or marker exists on the trade record, the live decision, or the journal `TradeRecord`. |
| Excluded from clean parity evidence | **✗ — D-1.** The forensic harness compares every matched trade. Its simulated reconciler clears the flag for data gaps (correct), but a trade exiting under an **unconfirmed** live-only gap would enter the trade-pair comparison unmarked. |

**What partly covers it today.** If the gap falls between the exit decision and the exit fill, `exit_fill_candle_missing` and `data_complete = False` are set. An exit decided **and** filled after an earlier gap (with the flag still active) is unmarked.

**Exact defect (D-1).** `PairSignalEngine.process_candle`'s exit branch does not record `self.requires_reconciliation` on the trade. It needs:
- a `reconciliation_affected` flag on the trade, plus the gap timestamps;
- propagation to the journal;
- exclusion from parity and statistics sets.

---

## 9. Mathematical dependencies

- **θ₀ = OLS(init window).** Identifiable iff ≥ 2 observations with var(ln P_B) > 0. Ill-conditioned (cond ~1e3–2e4) at the candidate L values, because ln P_B varies little relative to its level.
- **Kalman state:** a deterministic function of θ₀ and the data. θ₀'s influence decays at a data-dependent rate (§3 B/D).
- **First possible z:** seed + 336 candles, from the frozen warmup and z minimum.
- **C1:** h_t = e_t·R/S_t at the decision (the filtered residual), so it shares the innovation's sign. That holds unless the rolling innovation mean dominates, which is why the explicit check is needed.

## 10. Statistical dependencies

- OLS β on an init window is weakly identified for persistent spreads: RMSE 0.34–0.55 at half-life 120. θ₀ is a **starting value, not an estimate to be interpreted**.
- No inference may use θ₀.
- Under Design A, EG selection is independent of L. Kalman-based statistics are conditionally dependent on the init data, with a measured, decaying magnitude.
- Incomplete trades (C3) and reconciliation-affected trades (C5) must be excluded from inference sets; implementation is in Steps 13–17.
- The availability filter caused by L changes the selection family. It must be recorded **before** testing.

## 11. Quantitative dependencies

- L consumes L candles of pre-selection history per symbol, so symbol eligibility and the universe size depend on L.
- At the first possible signal, L changes z by ≤ 2×10⁻² (§3 B). That is negligible against the 2.0 threshold except at the margin, and irrelevant if no Kalman-based evidence is taken in S.
- **Live:** θ₀ is re-derived from exchange data at startup, so L also fixes how much history live must fetch (L + all candles since the seed).

## 12. Research-governance dependencies

- L must be frozen **before** any canonical seed exists, and before zones are applied.
- Changing L changes the protocol hash, which invalidates every seed and result (the intended behaviour).
- D-3, D-4 and D-5 must be closed before any canonical evidence is produced. Otherwise the invariant can be satisfied by legacy inputs.
- Legacy result files should gain explicit `LEGACY` provenance at their readers. They should not be modified in place: historical results are preserved.

---

## 13. Tests performed (read-only)

| Script (session scratchpad) | Purpose | Data |
|---|---|---|
| `l_synthetic.py` | §3 A–C | Synthetic; seeds 1006 / 1048 / 1120, 2006 / 2120, 3000; 400 reps (A, C), 40 reps (B) |
| `l_real.py` | §3 D | `data/{DODO,FIDA,MINA,ONG}_USDT_1h.csv`, first 4,400 common candles (2021-09 → 2022-04), stability only |
| `gov_demo.py` | §6 D-4 demonstration | Synthetic `dataset_a` |

- Code inspection: `signal_engine.py`, `pairs_bot_runner.py`, `config.py`, `health.py`, `entry_exit_engine.py`, `seed.py`, and every reader of `permutation_verdict` / `cointegration_pairs.csv` / `_kalman.csv` / walk-forward outputs.
- No repository tests were added: the governance defects are demonstrated by the scratch script. The existing suite was **not re-run**, because no code changed since the last green run (544 OK).

## 14. New defects discovered

| ID | Severity | Layer | Defect |
|---|---|---|---|
| **D-1** | HIGH (an approved condition is unmet) | E, C | Exits during reconciliation are not marked `RECONCILIATION_AFFECTED` and can enter parity and statistics sets |
| **D-2** | MEDIUM | E, A | Live C1 mismatch events are in memory only (lost on restart, not journaled); the runner logs them as `NONE \| <direction>`, hiding the reason |
| **D-3** | HIGH (before live or evidence) | E | Live entry eligibility comes from legacy, unversioned verdicts (`passes_filters`, `edge_appears_real`) |
| **D-4** | HIGH | E, C | The canonical engine accepts a legacy-seeded Kalman CSV and labels the result `STAGE2D-REVB-2` (demonstrated) |
| **D-5** | MEDIUM | E | No canonical data-version digest exists; canonical summaries carry no seed lineage |
| G-1 | LOW | E | Cointegration, permutation and walk-forward outputs carry no methodology tag (legacy status implicit) |

## 15. Owner decisions still required

1. **L**, as an explicit baseline. The evidence-based suggestion is 1000, with a mandatory L = 504 sensitivity re-run on development zones.
2. **Seed placement.** Recommended: Design A.
3. **Approve the fixes for D-1, D-2 and D-4**, the conditions for Step 12.
4. **Live eligibility under D-3:** suspend all entries until canonical verdicts exist (strict), or another rule.
5. **The data-version definition for D-5**, for example a SHA-256 of the canonical grid closes per symbol and range.
6. Unchanged from the decision lock: the zones (S, V, WF, F, L_H), the FX calendar and FX g_max, and the go-ahead for the reconciliation/backfill stage.

## 16. Exact prerequisites for Step 12

- **Required:** D-1, D-2 and D-4 fixed and tested, including a governance test that a legacy-lineage Kalman input is refused by `rule_set="CANONICAL"`.
- **Not required for Step 12 itself:** L, the zones, the FX calendar, D-3, D-5. These are required before Steps 13–20 and before any canonical evidence or live entry.
