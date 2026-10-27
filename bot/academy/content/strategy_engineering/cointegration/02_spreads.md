# What Is a Spread?

**In this lesson you will learn:** what a spread between two prices is, why one price is usually scaled first, and how our pairs research builds its spread.

## Why it matters

Correlation tells you whether two assets *move together*. Pairs research needs something different: it needs to know whether two prices *stay close together over time*. The quantity it watches to answer that is the **spread**.

## The idea in plain words

A **spread** is the gap between two prices. The simplest spread is just A − B.

Often the two assets are different "sizes". If one share of A tends to move about as much as two shares of B, comparing one with one would be unfair. So we scale B first: spread = A − 2 × B. The scaling number (here 2) is called the **hedge ratio**.

## An everyday example

Imagine two coffee shops in the same street. Shop A always charges a little more than shop B for a coffee. Prices at both shops go up and down over the years with costs, but the *gap* between them usually stays small. That gap is a spread. If one day the gap suddenly widens, you might wonder whether it will narrow again — or whether something has permanently changed.

## A worked example (illustration, not market data)

Two hypothetical shares over four days, with a hedge ratio of 2:

| Day | Price A | Price B | Spread = A − 2 × B |
|---|---|---|---|
| 1 | 102.0 | 50.0 | 102 − 100 = **2** |
| 2 | 104.0 | 51.5 | 104 − 103 = **1** |
| 3 | 101.0 | 50.0 | 101 − 100 = **1** |
| 4 | 106.0 | 52.5 | 106 − 105 = **1** |

Both prices moved around (A went from 102 to 106), but the spread stayed between 1 and 2. That is the kind of behaviour pairs research looks for.

## The mathematics — the spread our research actually uses

Our pairs research works with **log prices** (the natural logarithm of each price), so that moves are compared in percentage terms. It first fits a straight line between the two log prices on a *training* period only:

log(A) = α + β · log(B) + ε

using **ordinary least squares (OLS)** — the method that picks the line with the smallest sum of squared misses. Then the spread at each time t is:

spread_t = log(A_t) − α − β · log(B_t), using the α and β fitted on the training period.

Here β (the fitted slope) is the hedge ratio and α is a constant. Fitting on the training period only means no future data is used to build the spread.

## How Steph Quant Technologies uses it

The spread is the series whose behaviour the next two lessons examine: does it keep coming back to its usual level (stationarity), and how quickly (half-life)?

## Common mistakes

- **A small spread in the past is not a promise.** A spread that stayed in a band for years can break out permanently.
- **The hedge ratio is an estimate.** It carries error, and that error matters for the statistical test in lesson 4.
- **Regressing A on B is not the same as B on A** — they give different hedge ratios.

## Quick check

In the table above, what would the spread be on a day when A = 110 and B = 54? *(110 − 2 × 54 = 2 — still inside the usual range.)*

## Summary and next step

A spread is the gap between two prices after scaling one of them by a hedge ratio; our research builds it from log prices with a hedge ratio fitted by OLS on a training period. Next: **stationarity** — the property that tells us whether a spread keeps coming back.
