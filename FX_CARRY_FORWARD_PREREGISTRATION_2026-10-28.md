# FX carry — frozen forward paper position (pre-registration FXC-F1)

*claude code changed: 2026-10-28 — new. Step 3 of the FX carry study (steps 1–2: `fx_carry_accounting`,
`fx_carry_stress`). Owner instruction 2026-10-28: "commit and push, then go ahead with step 3", after the stress-test
proposal of a 5 % target volatility and a −10 % drawdown stop. This file is hashed at registration; the ledger records
the hash. Any change after registration is a NEW registration, never an edit.*

**Status: PAPER ONLY — no orders, no credentials, read-only MT5.** It is not edge evidence in the statistical sense:
a carry premium with a carry/volatility ratio near 0.3–0.6 cannot be confirmed statistically in less than decades. What
this test CAN show is whether the position behaves as designed on data nobody has seen: carry received vs promised,
realised volatility vs target, costs vs model, and whether the loss limit holds.

## Position (frozen)

| Item | Rule |
|---|---|
| Instruments | LONG USD/JPY and LONG USD/CHF only (the broker pays to hold both — carry accounting 2026-10-28) |
| Capital | $10,000 paper equity, never topped up |
| Clock start (T0) | the registration timestamp in the ledger; the first fill is at the close of the first full UTC day after T0 |
| Prices | daily close = last hourly close of the UTC day, from `data/forex` (kept current by `refresh_forex_data`) |
| Sizing | weights ∝ 1/σᵢ (equal risk), scaled so ex-ante portfolio volatility = **5 %** a year; σ and covariance from the trailing 60 trading days of daily returns ending the day BEFORE the rebalance |
| Rebalance | at T0's first fill, then the first trading day of each month |
| Lots | units rounded DOWN to 1,000 USD (0.01 lot), never sized up |
| Fills | buy at close + half the live spread, sell at close − half the live spread (broker snapshot of that day, else the latest) |
| Carry | the broker's LIVE swap (points per lot per day) from that day's snapshot, × calendar days since the previous close |
| Loss limit | equity ≤ 90 % of its running peak (−10 %) at any daily close → close everything at that close; the test is STOPPED and never restarts |
| Data scope | reads USD/JPY and USD/CHF only. P&L uses candles after T0 only. The volatility window at T0 includes candles between the FX research cutoff (2026-10-16) and T0 for these two pairs; neither is a QC-2 / v7 holdout pair, and no holdout pair is read |

## Checks (descriptive, declared now)

Reported monthly; judged at 12 months (or at a stop):

1. **Carry delivered:** carry booked ≥ 50 % of the carry the day-one swap snapshot implied for the same position and days.
2. **Risk as designed:** realised annualised volatility within 3 %–8 % (target 5 %).
3. **Costs as modelled:** total spread cost ≤ 0.10 % of equity a year.
4. **Loss limit held:** no daily close below −10 % from peak, or, if it fires, the position is flat at that close.

All four met → the design behaves as intended, and a decision about a small live trial can be considered (a new,
separate decision). Any failed → the reason is recorded and the design is not taken further as is. Neither outcome is
called an "edge".

## What will not happen

- No rule above changes after registration, including after a stop.
- No other pair is added, no parameter is re-fitted, no restart after a stop.
- Missing a daily update does not change results: the next update replays every missed day in order.
