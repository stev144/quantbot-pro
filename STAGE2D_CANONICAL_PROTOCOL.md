# Stage 2D Rev B — Canonical Research Protocol

**Methodology version:** `STAGE2D-REVB-4`.
- REVB-2 (2026-10-02): lifecycle C1/C2, H = 24, causal seed C6.
- REVB-3: L = 1000, Design A, provenance requirements (D-1…D-5, G-1).
- REVB-4 (2026-10-03): owner decision D-8 — seed candle bound to the selection-zone start (2021-11-07 00:00 UTC); research zones and the sealed forward holdout (F = 2026-10-04) in `bot/quant_core/research_zones.py` (`QBP-ZONES-1`).

See `STAGE2D_DECISION_LOCK.md` and `STAGE2D_D1_D5_G1_IMPLEMENTATION_REPORT.md`. The machine-readable values live in `bot/quant_core/protocol.py`.

This document defines what research **and** live must compute. It does not claim that any value is optimal or that any strategy is profitable.

**Status labels:**
- **FROZEN** = an owner decision of 2026-10-01;
- **FORCED** = follows from the mathematics;
- **OPEN** = not yet decided. Code fails closed on OPEN items.

---

## 1. Clock

| Symbol | Definition | Status |
|---|---|---|
| Candle k | Open time o_k on the hourly UTC grid; close c_k = o_k + 1 h. Every series is indexed by o_k | FORCED |
| Information cutoff | t₀ = c_t. Everything with close ≤ c_t is known | FORCED |
| Decision | Taken at t₀ | FORCED |
| Entry fill | Reference price = close of candle t + d, with d = 1 (canonical minimum) | FROZEN |
| Live equivalence | Live processes candle t+1, decides on candle t's values, and fills at close(t+1): the same cutoff and the same fill as research (Phase O, D13). **Not to be changed.** | FORCED |
| Exit decision | At cutoff c_u | FORCED |
| Exit fill | Close of u + d | FROZEN |
| Holding horizon H | Maximum **entry-fill → exit-fill** time. The time-stop decision is taken at entry + H − d, so the exit fill lands at entry + H. **H = 24** (baseline, not optimised) | FROZEN 2026-10-02 |
| First exit decision | E + 1. The entry-fill close is not also an exit-decision close (C2, information availability) | FROZEN |

**D3 label note.** The owner's instruction says "OPTION B" but defines H as the entry-to-exit-fill horizon, with exit fill = entry + H. That is option **A** of the Rev B blocker table. The explicit equations are implemented. The label is recorded here so the decision is unambiguous.

---

## 2. Kalman model

**State and noise:**
- State θ_t = [β_t, α_t]′. Random-walk transition θ_t = θ_{t−1} + w_t, with w ~ N(0, Q) and Q = diag(1e-5, 1e-5). FROZEN baseline.
- Observation x_t = H_t θ_t + v_t, with x = ln P_A, H_t = [ln P_B, 1], and v ~ N(0, R), R = 1e-3. FROZEN baseline.

**Recursion, per hourly grid candle t:**

| Step | Equation | Notes |
|---|---|---|
| Prior | θ(t\|t−1) = θ(t−1\|t−1); P(t\|t−1) = P(t−1\|t−1) + Q | |
| Innovation | e_t = x_t − H_t θ(t\|t−1) | |
| Innovation variance | S_t = H_t P(t\|t−1) H_t′ + R | **exposed** on both sides |
| Gain | K_t = P(t\|t−1) H_t′ / S_t | |
| Posterior | θ(t\|t) = θ(t\|t−1) + K_t e_t | |
| Covariance (Joseph form) | P(t\|t) = (I − K_t H_t) P(t\|t−1) (I − K_t H_t)′ + K_t R K_t′ | |

**Missing candle** (on the grid, within the gap policy): no update. θ(t\|t) = θ(t\|t−1), P(t\|t) = P(t\|t−1). A run of g missing candles therefore gives P(t+g\|t) = P(t\|t) + g·Q. **FORCED** by the transition model.

**Signal quantities at cutoff t (FROZEN, owner D1/D2):**
- β_e = β(t\|t), α_e = α(t\|t);
- uncertainty used for decisions = P(t\|t)[0,0]. This is a **variance**.

**Initialisation (C6, causal; Design A):**
- θ₀ = OLS of ln P_A on [ln P_B, 1] over the init window only: the **L = 1000** grid candles **before** the seed candle. L is a frozen baseline convention, not an optimum; L = 504 is a development sensitivity check only.
- P₀ = p0_scale · I, with p0_scale = 1.
- The init window lies entirely before the selection zone, and the selection zone starts at the seed candle.
- The Kalman state keeps a fading, nonzero dependence on the init data. Independence is **not** claimed. Research and live must use **the same seed candle and the same θ₀**. A stale or incompatible persisted state is rejected (FROZEN, D9).

---

## 3. Gap policy (FROZEN, owner D8)

g_max = 3 hourly candles. Let g be the number of consecutive missing candles.

| Case | Treatment |
|---|---|
| 1 ≤ g ≤ 3 | Keep the grid. Each missing candle is a prediction-only step (P += Q). Nothing is compressed |
| g > 3 | The state becomes **invalid** and new signals are suspended. At the next observed candle the filter re-seeds with the canonical θ₀, P₀, the z history restarts, and the warmup counter restarts |
| Warmup | 168 grid candles after any (re)seed produce no z and no signal |

Research and live apply the same policy.

---

## 4. z-score, side and spreads

**z-score:**
- z_t = clip((e_t − mean_W)/sd_W, ±5);
- W = the trailing 504 **grid** candles of the current state segment, current candle included;
- sd uses ddof = 1;
- at least 168 valid (non-missing, post-warmup) values are required, otherwise z_t is undefined.

All of these values are FROZEN.

**Side:** σ_t = −sign(z_t)·1{\|z_t\| ≥ 2.0}.
- σ = +1 is LONG_SPREAD: long A, short β_e·B.
- σ = −1 is SHORT_SPREAD.

**Held spread** (FROZEN, owner): h_u = ln P_A,u − β_e ln P_B,u − α_e, using the entry values only.

**Normalisation exit (FROZEN 2026-10-02):** LONG exits when h ≥ 0, SHORT exits when h ≤ 0. The derivation is in `STAGE2D_DECISION_LOCK.md` Decision 1.

**Entry sign condition (C1):** LONG requires h_t < 0 and SHORT requires h_t > 0 at the decision cutoff. Otherwise the result is `ENTRY_SIGN_MISMATCH`: recorded, no trade.

**Time stop:** the exit decision is at entry fill + H − d.

The canonical rule set has **no** partial exit, target exit or z stop-loss. It is ENTRY → HELD → normalisation exit or time stop.

---

## 5. Target, P&L and costs

**Target:**

r_{t,d,h} = σ_t[(P^A_{t+d+h}/P^A_{t+d} − 1) − β_e(P^B_{t+d+h}/P^B_{t+d} − 1)]/(1+β_e)

With zero costs this equals `pnl.py`'s mark P&L ÷ T (tested to 1e-12).

**P&L:** `bot/pairs/pnl.py` (unchanged):
- N_A = T/(1+β), N_B = β·N_A;
- q_A = σN_A/M_A, q_B = −σβN_A/M_B;
- per-fill slippage s and fee f;
- borrow on the short leg = \|q\|·r_h·⌈hours⌉·F_exit.

**Costs:**
- research uses declared fee and slippage;
- live records actual costs;
- unknown borrow is **COST_UNAVAILABLE**, never zero (FROZEN, D11).

---

## 6. Sizing (FROZEN: fixed fraction)

- T = f · C_strategy, with f = 0.05 and a hard maximum f_max (= 0.05; the owner gave no other value).
- N_A = T/(1+β), N_B = β·T/(1+β), with β finite and > 0.
- **Reject:**
  - β ≤ 0 or non-finite β;
  - a quantity ≤ 0;
  - a quantity below minQty after flooring to stepSize;
  - notional below minNotional, at entry and at exit prices.
- There are **no** Kelly inputs, z-multipliers or uncertainty multipliers.

---

## 7. Quantitative assumption register

None of these values is claimed to be optimal. Status is either *baseline, unvalidated* (frozen without evidence of optimality) or OPEN.

| ID | Name | Value / definition | Basis | Possible bias | Sensitivity required | Status |
|---|---|---|---|---|---|---|
| Q1 | Kalman Q/R | Q = diag(1e-5, 1e-5), R = 1e-3 | legacy baseline | only Q/R matters in steady state; uncertainty scale depends on it | Q/R grid on validation only, versioned | baseline, unvalidated |
| Q2 | z window | 504 / 168 / clip 5 / warmup 168 | legacy | window length vs half-life | yes | baseline |
| Q3 | entry threshold | \|z\| ≥ 2.0 | convention | selection on history | yes | baseline |
| Q4 | exit definition | held-spread normalisation (LONG h ≥ 0, SHORT h ≤ 0) or time stop | owner | — | — | FROZEN |
| Q5 | partial exit | none | owner | — | — | FROZEN |
| Q6 | holding horizon H | 24 candles, entry fill → exit fill | owner 2026-10-02 | not optimised; may not match the half-life | yes, on development data only | baseline, unvalidated |
| Q7 | entry delay | d = 1 candle | forced minimum | understates live latency | d ∈ {1, 2, 3} | baseline; production d OPEN |
| Q8 | fees / slippage | 0.1 % / 0.05 % per fill | published fee; flat slippage | slippage optimistic | stress | declared |
| Q9 | borrow | unavailable unless measured | owner D11 | omission | break-even rate | FROZEN treatment |
| Q10 | cointegration threshold | EG p < 0.05 | legacy | — | — | unvalidated |
| Q11 | half-life | exact AR(1) on the fixed-β residual, no fallback | Stage 2C | biased low at long half-lives | CI | FORCED |
| Q12 | data minimum | per protocol zone | — | — | — | OPEN (zones) |
| Q13 | prefilter | selection zone only, recorded | Stage 2C | family shrinkage | — | rule FORCED; threshold OPEN |
| Q14 | permutations | B = 999, timing null | owner | resolution | B + 1 ≥ m/q | baseline |
| Q15 | walk-forward | chronological, purge/embargo derived | Stage 2C | — | — | OPEN (zones) |
| Q16 | uncertainty | posterior P(t\|t)[0,0] (variance) | owner D1 | scale-dependent | — | FROZEN definition |
| Q17 | sizing | fixed fraction f = 0.05, f_max = 0.05 | owner | — | — | FROZEN (not optimal) |
| Q18 | β domain | β > 0 | forced | — | — | FORCED |
| Q19 | gap policy | g_max = 3, P += gQ, re-seed beyond | owner D8 | — | — | FROZEN |
| Q20 | FDR | BH, q = 0.05 | legacy | PRDS unproven: **conditional guarantee only** | dependent-family simulation | unvalidated |

---

## 8. Open items that block implementation

1. **Normalisation-exit sign** (section 4): a mathematical conflict in the frozen rule.
2. **Value of H** (Q6).
3. **Temporal-partition zone boundaries:** needed before the selection-zone Engle–Granger, purge/embargo and walk-forward work can use real data.
4. **Forex under the gap policy (new contradiction).**
   - Forex 1h data has a weekend closure of about 48 h, every week. Under D8 (g_max = 3) that is a gap > 3, so the state is invalidated and re-seeded every weekend.
   - Warmup counts 168 *grid* candles, weekend hours included. One trading week (about 120 h) plus the next weekend reaches 168 h exactly when the next gap invalidates the state again.
   - A forex Kalman pair therefore never leaves warmup and never signals. D8 is applied as written; whether market-closure hours are "missing" for forex is an owner decision.

## 9. Implementation status (2026-10-01)

| Step | Status | Where |
|---|---|---|
| 1 Protocol/config | done | `bot/quant_core/protocol.py` |
| 2 Math spec | done | this document |
| 3–4 Posterior parity, S exposed | done | `bot/quant_core/kalman.py` (one `measurement_update`, used by research `_run_filter`, live `PairSignalEngine`, `kalman_online.kalman_step`) |
| 5 Gap/time grid | done | research `_align_prices` keeps the complete hourly grid; live steps missing grid candles from timestamps; gap > g_max re-seeds both identically |
| 6 Canonical init (D9) | done | `bot/quant_core/seed.py`. Research writes `research_data/kalman_seeds.json`. The live runner replays every candle from the seed, or refuses entries |
| 7 Entry metadata (D5) | done | `entry_decision_timestamp`, `entry_alpha`, `entry_beta` (full precision), `entry_held_spread`, entry prices on both sides |
| 8 Fixed-fraction sizing | done | `FixedFractionSizer`; Kelly removed. Live C = $500 total pilot capital, so T = $25. Live real mode floors to stepSize and checks minQty/minNotional before any transfer; research is `UNVERIFIED` |
| 9 Held-spread exit + C1 sign check | done (2026-10-02) | `bot/quant_core/lifecycle.py`; research `EntryExitEngine(rule_set="CANONICAL")`; live `PairSignalEngine`. Exit sign resolved: LONG exits at h ≥ 0, SHORT at h ≤ 0 (see `STAGE2D_DECISION_LOCK.md`) |
| 10 Time stop (H = 24 baseline) | done | decision E + H − 1, fill E + H; first exit decision E + 1 (C2) |
| 11 Missing/skipped candles | done (minimum fail-closed) | C3 flags; C5 live reconciliation block. Backfill deferred |
| C6 causal seed | done: L = 1000 (baseline), Design A | `bot/quant_core/seed.py`, `KalmanFilterEngine.run_canonical` |
| D-1…D-5, G-1 provenance | done | see `STAGE2D_D1_D5_G1_IMPLEMENTATION_REPORT.md` |
| 12–20 | not started | zones (BLOCKED), L (BLOCKED) |

Methodology version is now `STAGE2D-REVB-2`.

**Live capital basis (step 8).** With fixed fraction, the strategy capital is the $500 total pilot capital, so T = 0.05 × 500 = $25 per pair trade. Sizing off the former $250 per-pair split would give T = $12.50, which is below the $15 live floor, so no pair could ever trade.
