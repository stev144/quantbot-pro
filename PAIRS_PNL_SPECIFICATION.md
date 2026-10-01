# Pairs P&L Specification (Phase 1: quantitative ground truth)

**Status:** draft for independent review, 2026-09-29.
**Scope:** the P&L of one two-leg pairs position, from entry to final exit, including partial exits. This defines *what a trade earns*. It says nothing about *when* to trade (signals), how big the trade should be (sizing policy), or whether any strategy has an edge.
**Replaces:** every earlier research P&L formula, including `expm1(±Δkalman_spread) − 0.3%` (audit C-01) and the interim fixed-quantity formula with a flat 0.3% cost. Neither is preserved for compatibility.

---

## 1. Notation

| Symbol | Meaning | Unit |
|---|---|---|
| A, B | the two legs (`symbol_a`, `symbol_b`) | — |
| M_i,t | mark (reference) price of leg i at time t; in research, the candle close | USDT per unit |
| d | spread direction: +1 = LONG_SPREAD, −1 = SHORT_SPREAD | — |
| β | hedge ratio *frozen at entry* | dimensionless |
| T | target gross notional at entry | USDT |
| q_i | **signed** quantity of leg i held: + long, − short | units of base asset |
| f | fee rate per fill, as a fraction of fill notional | — |
| s | slippage per fill, as a fraction of the mark price | — |
| r_i | borrow interest rate on leg i's base asset, per hour | fraction of borrowed units per hour |
| φ | fraction of the *current* position closed by an exit, 0 < φ ≤ 1 | — |

## 2. Hedge ratio interpretation

The strategy's spread is `log M_A − α − β·log M_B`. For small moves, d log A ≈ β·d log B. So β is a **return (dollar-per-dollar) hedge ratio**: to neutralize one dollar of A exposure you hold β dollars of *opposite-signed* B exposure.

* β is **not** a unit ratio. Unit quantities follow from prices (§3).
* The position stays at the β observed at entry. Quantities are **fixed** after entry, and later re-estimates of α or β do not change what is held or what it earns. There is no rebalancing.
* The sign of β decides the side of B:
  * **β > 0:** B is on the opposite side to A (long A / short B for LONG_SPREAD).
  * **β < 0:** B is on the *same* side as A (long A / long B for LONG_SPREAD). It is still the position whose value tracks the spread.
  * **β = 0:** no B leg. The position is a naked, directional A position, not a hedge.

Whether β ≤ 0 positions *should* be traded is a policy question for the sizing layer (audit H-02) and outside this specification. This specification only defines them correctly, so they can no longer produce negative or unbounded notionals.

## 3. Fixed-leg quantities and entry notional

The gross notional is split so that the B-dollars are |β| times the A-dollars and both add up to T:

```
N_A = T / (1 + |β|)
N_B = T·|β| / (1 + |β|)          N_A + N_B = T  for every β
```

Quantities are sized from the **entry mark prices**, the same way the live executor sizes an order from a ticker price (`qty = usdt / price`):

```
q_A = d · N_A / M_A,0
q_B = −d · β · N_A / M_B,0        (so |q_B|·M_B,0 = N_B, and its sign is −d·sign(β))
```

**Entry notional (at mark):** `E₀ = |q_A|·M_A,0 + |q_B|·M_B,0 = T`.
**Entry notional (at fill):** `Σ_i |q_i|·F_i,0`, where F is defined in §5. It differs from T only by slippage.

## 4. Mark-to-market (frictionless) P&L

For the slice of the position closed at time t (φ of the current quantities):

```
MarkPnL = φ · Σ_i q_i · (M_i,t − M_i,0)
```

This contains no α, β, spread, z-score or model term. It is the price P&L of the quantities held.

## 5. Slippage

Every fill is adverse by s relative to the mark: buying pays more, selling receives less.

```
F = M·(1 + s)   when the fill is a buy
F = M·(1 − s)   when the fill is a sell
```

The side of each fill follows from the sign of q_i:
* At entry, q_i > 0 is a buy and q_i < 0 a sell (short: borrow then sell).
* At exit, each side reverses.

Slippage cost of one fill: `|q|·M·s`.

For a slice: `Slip = φ·s·Σ_i |q_i|·(M_i,0 + M_i,t)`

Equivalently, the price P&L *at fill prices* is `FillPnL = MarkPnL − Slip`.

## 6. Per-leg fees

Every fill pays f on its **fill** notional:

```
Fee(fill) = f · |q| · F
Fees(slice) = φ · f · Σ_i |q_i| · (F_i,0 + F_i,t)
```

That is 4 fills per full round trip: 2 legs × entry and exit.

## 7. Borrow interest

Only a leg with q_i < 0 is borrowed. Its borrowed quantity is |q_i| units of the base asset. Interest accrues in the **base asset** and must be bought back and repaid at exit:

```
H        = max(1, ceil(hours_held))           accrual periods (see assumption A4)
Interest units (slice) = φ · |q_i| · r_i · H
Borrow  (slice) = Σ_{i: q_i<0} φ · |q_i| · r_i · H · F_i,t      (F_i,t is the exit buy fill)
```

`hours_held` for a slice is measured from the position's entry to that slice's exit.

## 8. Net P&L

```
Net(slice) = MarkPnL − Slip − Fees − Borrow
Net(trade) = Σ over all slices (partial exits + final exit)
```

## 9. Partial exit

A partial exit closes fraction φ of the **current** quantities of *both* legs at the same time t. It realizes `Net(slice)` per §4–§8, with entry-side slippage and fees allocated pro rata: the slice carries φ of each. The remaining position keeps `(1 − φ)·q_i`, its original entry prices and its original entry time. The final exit is a slice with φ = 1 of whatever remains. So a trade's total slippage, fees and quantities are conserved: the slices sum exactly to the unsliced trade.

## 10. Return on capital

**Capital** is the USDT committed at entry, which is the entry gross notional at mark, **C = T**. This matches the live isolated-margin design: leg A's notional is transferred in and spent (long) or posted as collateral (short); the same holds for leg B.

```
ROC(trade) = Net(trade) / C
```

Win or loss is defined on `Net(trade) > 0`.

The research trade log reports whole-trade figures, all as fractions of C:
* `gross_pnl_pct = Σ MarkPnL / C`
* `fee_cost_pct = Σ(Slip + Fees + Borrow) / C`
* `net_pnl_pct = ROC`

## 11. Reference hand calculation (used verbatim by the tests)

Inputs: T = 1000, β = 1.5, LONG_SPREAD, M_A: 10 → 11, M_B: 20 → 21, f = 0.001, s = 0.0005, r_B = 0.0001/h, held 5h.

| Quantity | Value |
|---|---|
| N_A, N_B | 400, 600 |
| q_A, q_B | +40, −30 |
| MarkPnL | 40·1 + (−30)·1 = **10.000000** |
| Fills | A buy 10.005 / A sell 10.9945; B sell 19.99 / B buy 21.0105 |
| Slip | 40·(0.005 + 0.0055) + 30·(0.01 + 0.0105) = **1.035000** |
| Fees | 0.001·(40·10.005 + 30·19.99 + 40·10.9945 + 30·21.0105) = **2.069995** |
| Borrow | 30·0.0001·5·21.0105 = **0.31515750** |
| Net | 10 − 1.035 − 2.069995 − 0.3151575 = **6.5798475** |
| ROC | **0.0065798475** |

## 12. Assumptions (explicit, each to be reviewed)

* **A1:** Research fills happen at the candle close used as the mark (the `*_kalman_LAG1h` convention: decision lagged one candle, fill at the current close). No intrabar path.
* **A2:** Slippage is a constant fraction of price, independent of order size or book depth. The audit measured FIDA at ~10× the modeled rate at $10k per leg, so this is **not** conservative for thin legs.
* **A3:** Full fills; no partial fills; both legs fill at the same timestamp (no leg risk).
* **A4:** Binance charges isolated-margin interest hourly, with the first hour charged at borrow. H = max(1, ceil(hours)) approximates that. The exact accrual schedule is unverified.
* **A5:** The borrow rate is constant over the holding period. Historical borrow rates are not available in this repo; see Q1.
* **A6:** The fee on buying back the *interest* units (f × Borrow) is omitted, as a second-order term.
* **A7:** Capital = T (unleveraged collateral). Excess margin, margin calls and liquidation are not modeled.
* **A8:** Fees are paid in USDT-equivalent. The live fee-in-base-asset effect (audit H-06) changes the quantity available to sell, not the P&L definition here.
