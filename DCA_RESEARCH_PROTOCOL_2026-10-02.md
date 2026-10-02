# DCA Research Protocol: Canonical Clock, Fill Models, Accounting, Nulls

**Status:** specification, written **before** the Phase 2 implementation (master prompt, B2).
**Scope:** `bot/research/dca_engine.py` only. **Research-only:** not connected to live trading or the dashboard.

## 1. Canonical clock (B2)

Notation:
- Candle *c* covers `[t_c, t_c + Δ)`.
- Its OHLCV is known only at **close(c) = t_c + Δ**.
- Δ is the series' modal bar interval.

| Rule | Definition |
|---|---|
| D1. Decision instants | Decisions are made **only at candle closes**. The information set at close(c) is all OHLCV of candles ≤ c, and nothing else. |
| D2. Market execution | An action decided at close(c) executes at **open(c+1)**: the next *available* candle, whatever its timestamp (a gap does not invent prices). The declared adverse slippage and fee apply. |
| D3. Cycle succession | An exit decided at close(c) executes at open(c+1). The next cycle's entry is decided at close(c+1) and executes at open(c+2). A cycle never opens at the instant another closes: no zero-time round trip on the same print. |
| D4. Exit logic | Profit activation and the trailing stop are evaluated on **closes**; the exit executes per D2. |
| D5. Timeout | `max_cycle_hours` (or `max_cycle_candles × Δ`) is measured in **elapsed wall-clock time**, from the entry fill to the decision close. Never in rows. |
| D6. Data end | A cycle still open at the last close is **CENSORED**: never closed with a fabricated fill. It is reported separately, marked at a liquidation value (close × (1 ∓ slippage) − exit fee) that is labelled as a mark. An exit decided at the last close cannot execute and is also censored (`exit_pending_at_data_end`). |

## 2. Ladder fill models (B1)

OHLC does not reveal the order of prices within a candle. Each model below is stated so that it never needs that order.

| Model | Ladder levels | Identifiable from OHLC? | Exposure bias |
|---|---|---|---|
| **NEXT_OPEN** (default) | Close-confirmed: the adverse move of close(c) vs the last fill ≥ the level's drop → market order at open(c+1). At most **one level per decision**: the next level's trigger is relative to a fill price not yet known. | Yes | **Slowest** deployment: one level per close |
| **INTRABAR_LADDER** | Resting limit orders, placed when each fill completes, at the known trigger T_k = P_{k−1}·(1 ∓ d_k). In candle *c*: if the open has already passed T_k, fill at the open (gap); else if low (high, for SHORT) reaches T_k, fill at T_k. Repeat for T_{k+1}, relative to that fill, within the same candle. | **Yes.** On a continuous path from the open to the low, price crosses T_k before T_{k+1} < T_k, so the fills are deterministic. | **Fastest** deployment: touch = fill; no queue risk; no limit slippage |
| **LEGACY_CLOSE** | The pre-2026-10-02 behaviour: decide **and** fill at close(c); a new cycle opens at the exit close | n/a | Zero-latency, same-print; **not canonical**. Kept only so earlier results reproduce. |

- Exits are close-decided in every model, so no same-candle entry-vs-exit ordering question arises.
- **No model is called "conservative."** NEXT_OPEN and INTRABAR_LADDER bracket **how fast the ladder deploys** in a crash: one level per close versus every crossed trigger within the candle.
- **They do NOT bracket P&L or drawdown.** INTRABAR fills at the trigger prices, NEXT_OPEN at the next open after a crashed close, so either can show the larger loss.
  - *Correction, 2026-10-02:* an earlier draft of this table said the models bracket "exposure". Real BTC/USDT data (2021–2026) showed INTRABAR with the **smaller** maximum unrealised loss (−118.9 vs −298.6 USDT), so the P&L ordering is an empirical question.
  - A result is only robust if it survives both models.

## 3. Accounting (B3, B6, B8)

- **Mark-to-market equity at every close:**

      E_c = realised P&L of closed cycles
          + [open cycle: qty·close·(1 ∓ s) − exit fee − cost basis − entry fees − financing accrued (if KNOWN)]

- **Reported separately, never summed silently:**
  - realised P&L of resolved cycles (profit exits, timeouts);
  - censored (data-end) MTM P&L;
  - total MTM P&L = their sum, labelled as such.
- **Tail metrics:**
  - maximum capital deployed;
  - maximum ladder depth;
  - maximum unrealised loss;
  - maximum MTM drawdown (USD and % of the theoretical full-ladder deployment);
  - time in cycle;
  - longest cycle (hours);
  - longest underwater period (hours) and whether it is unrecovered at the data end;
  - worst open-cycle loss;
  - ladder exhaustion against the **configured** level count (B7).
- **Financing status** (per cycle and per summary):

  | Status | Meaning |
  |---|---|
  | `NOT_APPLICABLE` | Crypto spot LONG |
  | `KNOWN` | A rate per hour is supplied; it is charged on the cost basis per elapsed hour |
  | `UNAVAILABLE` | Crypto SHORT borrow, FX swap/rollover. **Not charged and not presented as zero:** `cost_complete = False`. |
  | `ASSUMED_ZERO` | Explicit opt-in only, labelled |

- **Gaps (B8):** Δ is the modal interval. A gap is any interval > Δ.
  - Per cycle: `gap_count`, `gap_hours` and `holding_hours` (elapsed) are recorded; `holding_candles` counts observed candles.
  - No prices are invented. A decision before a gap executes at the first open after it.

## 4. Nulls (B4)

**What the legacy null preserves:** it block-shuffles close-to-close log-returns. Their sum is invariant, so the start and end prices, and therefore the drift, are preserved **exactly**. It destroys ordering beyond the block length. It can only test "does the order of the path matter, given this exact drift". It **cannot** test whether the profit comes from drift.

**Canonical nulls** (OHLC-aware: each candle is the tuple log(o, h, l, c / previous close), so intrabar shape and gaps are preserved):

| Null | Preserves | Destroys | Question it answers |
|---|---|---|---|
| `BLOCK_SHUFFLE` | Drift, endpoints, block-local structure, candle shapes | Ordering beyond block length | Does the real **path order** matter, given its drift? (path dependence) |
| `DRIFT_REMOVED_BLOCK_SHUFFLE` | Candle shapes, volatility, block-local structure | Drift (each candle's log levels shifted by −μ, so the terminal price equals the start) | Does the strategy make money **without** the drift? |

How to read the two together:

| Beats `BLOCK_SHUFFLE` | Beats `DRIFT_REMOVED` | Reading |
|---|---|---|
| no | yes | Profitability is explained by **drift** (beta), not DCA mechanics |
| yes | yes | Consistent with path-structure edge, subject to the family correction |
| no | no | No evidence |

Further rules:
- **Block-size sensitivity:** run at several block lengths (default 24 h, 168 h, 720 h). A verdict that flips with block size is **not robust**.
- **Resolution:** the empirical p-value is (k + 1)/(B + 1), so the smallest attainable p is 1/(B + 1). B < 999 → assessment `INSUFFICIENT_RESOLUTION`: no claim is possible.
- **Statistics tested:** total MTM P&L and MTM P&L / max capital deployed (both required: an intersection-union test, valid at α without correction across these two).
- **Multiple testing across symbols, configs, nulls and block sizes** is the caller's family and must be corrected (BH or Bonferroni) **before** any claim. The engine reports raw p-values and the family size it ran.
- No permutation result here is "statistical significance" until the family correction is applied and the null is shown appropriate.

## 5. Volatility scaling (B5)

- A volatility scale factor is a **fitted parameter**. It may use only:
  - a pre-specified calibration window `[calibration_start, calibration_end)`; or
  - inside walk-forward, the **purged training slice** of each fold (`fit_fn`).
- Full-history scaling is refused.
- **Positive control:** a test where the future volatility differs proves the fitted factor equals the prefix-only computation.

## 6. Out of scope here
- Research filters (regime, z-score, support/resistance, liquidity): after the baseline is trustworthy (master prompt §10).
- `r_multiple` as return-on-deployed-capital in an R-based Sharpe (M-06/H-11): Phase 3.
