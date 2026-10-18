# Full-Ladder Stop Rule — Pre-Registration

**Registration date:** 2026-10-18
**Status:** PRE-REGISTERED — threshold NOT yet chosen. Do not tune, test, or implement until the test window closes.
**Pair scope:** GBP/JPY LONG only (the only QC-1 holdout-validated pair)
**Related protocol:** FX-QC-PROTOCOL-1 (`FX_DCA_PROTOCOL_2026-10-17_v7.md`)

---

## 1. Motivation

A full-ladder loss scan run on 2026-10-18 (commit `928e43c`, branch `stage2d-revb`)
found that every QC-1 pair has at least one cycle in which all 7 ladder levels filled
and the position still closed at a loss via `TIME_EXIT`:

| Pair | Date | Loss at $3K notional/entry | Macro event |
|------|------|-----------------------------|-------------|
| GBP/JPY | 2024-07-30 → 08-02 | −$6,371 | BOJ surprise rate hike; yen carry unwind |
| GBP/JPY | 2026-08-31 → 09-03 | −$1,451 | — |
| EUR/JPY | 2024-07-31 → 08-05 | −$5,496 | Same BOJ event |
| EUR/USD | 2025-07-28 → 07-31 | −$2,861 | USD strength spike |
| AUD/CAD | 2025-04-02 → 04-07 | −$9,127 | Trump tariff shock |
| NZD/CHF | 2025-04-02 → 04-07 | −$3,556 | Same tariff shock |
| NZD/CHF | 2023-10-11 → 10-16 | −$232 | — |

All losses exit via `TIME_EXIT` — the 72-candle clock ran out before price recovered.
None were stopped mid-ladder; the loss deepened as each DCA level triggered into a
sustained macro move. The ladder amplifies exposure in exactly the conditions where
it cannot recover.

GBP/JPY's net P&L remains positive despite absorbing the −$6,371 BOJ event, but the
tail risk is real and must be managed before any live deployment.

---

## 2. The rule (pre-registered)

**Rule type:** Intra-cycle unrealised-loss stop, applied only to full-ladder cycles.

**Formal statement:**

> At every candle close, if a cycle has `fills == ladder_depth` (all levels have
> triggered) AND the mark-to-market unrealised loss exceeds `threshold`, queue that
> cycle for `EXIT` with `exit_reason = "FULL_LADDER_STOP"`.

**Implementation point:** Immediately after the existing `TIME_EXIT` check in the
candle-close block of `_simulate_data_derived_next_open` (and the paper engine's
simulation loop). The stop fires only when the ladder is fully deployed — it does
not interfere with normal DCA fills.

**Unrealised loss definition:**
```
unrealised_loss = (close_price - wap) * total_qty   # negative = adverse
```
Stop fires when `unrealised_loss < -threshold`.

---

## 3. Threshold candidates (pre-specified, not yet selected)

The threshold is expressed as a multiple K of `entry_notional` (currently $3,000):

| K | Threshold ($) | Meaning |
|---|---------------|---------|
| 1.0 | $3,000 | 1× entry notional; tight |
| 1.5 | $4,500 | 1.5× entry notional |
| 2.0 | $6,000 | 2× entry notional |
| 2.5 | $7,500 | 2.5× entry notional; near current worst case |

**No other values will be tested.** Testing values outside this pre-specified set
after seeing the test window data is p-hacking.

---

## 4. Selection criterion (pre-specified)

The threshold K* is selected as the **smallest K in the candidate set** such that,
over the test window:

1. At least one `FULL_LADDER_STOP` event is observed (the stop actually fired), AND
2. Total net P&L over the test window with the stop ≥ 80% of total net P&L without
   the stop (the stop does not destroy the strategy's overall edge).

If no K satisfies both conditions, the result is **INCONCLUSIVE** — collect more data.
If no full-ladder event occurs during the test window, the result is **NO EVENT** —
the rule cannot be validated or invalidated; extend the window.

Rationale for "smallest K": prefer the tightest stop that still preserves edge,
because the purpose is loss containment, not just any stop that happens to work.

---

## 5. Test window

- **Start:** 2026-10-18 (this registration date — the day after the holdout was consumed)
- **Minimum length:** 90 calendar days (approximately 1,080 × 1h candles for GBP/JPY)
- **Earliest evaluation date:** 2027-01-17
- **Data source:** GBP/JPY paper simulation on live-fetched MT5 data, same pipeline as
  QC-1 (`FX_DCA_PROTOCOL_2026-10-17_v7.md`)

The test window is new data — it does not include any candles used in QC-1 calibration,
walk-forward folds, or the holdout. Using pre-2026-10-18 data to tune or validate the
threshold would be pseudo-holdout and is prohibited.

---

## 6. What counts as validated

The rule is **VALIDATED** if:

- K* is selected by criterion §4 above
- The simulation with K* shows ≥ 1 stop event during the test window
- Total P&L with K* ≥ 80% of total P&L without K* over the same window
- The result is reproducible: re-running the simulation on the same test window data
  produces the same K* selection

The rule is **INVALIDATED** if:

- No K in the candidate set satisfies criterion §4 (including the case where the
  stop fires but destroys >20% of edge at every K)

---

## 7. What must NOT happen before the test window closes

- Do not implement any stop in the live or paper simulation code
- Do not look at the test-window data and then choose K
- Do not extend the candidate set based on interim results
- Do not apply the rule to any pair other than GBP/JPY without a separate registration

---

## 8. Governance chain

- **CRRP-2026-10-02:** No strategy has canonical edge evidence; all history is
  pseudo-holdout. This pre-registration follows from that constraint — the rule
  cannot be validated on data already seen.
- **QC-1 holdout consumed:** 2026-10-18 (GBP/JPY +$9.55 at $6/cycle). The test
  window starts immediately after.
- **IS_PAPER_ONLY = True:** Must remain True until the full governance chain
  (QC-1 edge evidence + stop-rule validation + live deployment sign-off) is complete.

---

## 9. Sign-off

Pre-registered by: Steven Bwenje
Assisted by: Claude Sonnet 4.6
Date: 2026-10-18
Branch: stage2d-revb
Triggering commit: 928e43c (Paper Trader: remove ranging gate)

This document must not be modified after registration. Any change to the rule,
threshold candidates, selection criterion, or test window constitutes a new
registration and must be saved as a new file with a new date.
