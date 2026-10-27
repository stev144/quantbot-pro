# Cointegration and the Engle–Granger Test

**In this lesson you will learn:** what cointegration is, how the Engle–Granger test works step by step, why its critical values differ from the ordinary ADF test, and what a positive result does — and does not — mean.

## Why it matters

You now have the three building blocks: correlation (moving together), the spread (the gap), and stationarity (coming back). Cointegration puts them together into one precise question.

## The idea in plain words

Two prices that are each non-stationary — each can drift anywhere — are **cointegrated** if some combination of them (a spread) is stationary. They wander, but they wander *together*.

Picture two dog walkers in a park, joined by a loose leash. Each person's path is unpredictable, but the distance between them can never grow without limit. The leash is the cointegrating relationship.

## Cointegration is not correlation

- Two coins can have highly correlated *returns* while their *prices* slowly drift apart for ever — correlated, not cointegrated.
- Two prices can stay tied together over months while their day-to-day returns are only loosely correlated — cointegrated, not strongly correlated.

## The Engle–Granger test, step by step

1. **Fit the line.** On the training period only, fit log(A) = α + β · log(B) + ε by ordinary least squares. The leftovers (the residuals) are the spread from lesson 2.
2. **Test the leftovers.** Run an ADF-type unit-root test on the spread. The null hypothesis is *no cointegration* (the spread is non-stationary).

## Why the critical values are different

In step 1, OLS chose α and β to make the leftovers as small and as steady-looking as possible. So even two completely unrelated random walks produce a spread that looks *more* stationary than it really is. The ordinary ADF tables don't allow for that, and using them would find "cointegration" far too often.

The fix is to use **MacKinnon's residual-based critical values**, which account for the fitted line. For two series with a constant, in large samples, the 5% critical values are:

| Test | 5% critical value |
|---|---|
| Ordinary ADF (one series, constant) | **−2.86** |
| Engle–Granger (two series, constant) | **−3.34** |

**Worked example:** suppose the test statistic on a spread is **−3.0**. Against the ordinary ADF table it looks significant (−3.0 is below −2.86). Against the correct Engle–Granger table it is **not** (−3.0 is above −3.34). Using the wrong table would have declared a cointegrated pair that the correct test does not support.

## How Steph Quant Technologies does it

Our cointegration research:

- works on **log prices** and fits the hedge line on the **training window only**;
- runs the Engle–Granger test with **MacKinnon residual-based p-values** (records the plain ADF p-value only as a diagnostic);
- corrects for testing many pairs at once with the **Benjamini–Hochberg false-discovery-rate** procedure;
- filters by **half-life** — how many candles the spread takes to close half its gap, estimated as −ln 2 / λ, where λ comes from a regression of the spread's change on its previous level (for example, λ = −0.1 gives about **6.9 candles**);
- re-checks the spread on a later **validation window** with the training coefficients frozen.

The platform's evidence registry classifies its earlier pairs results as **invalidated or superseded** (a profit-measurement error and a flawed permutation test were found), and no strategy currently has established edge evidence. This course teaches the method; it does not claim the method has found a profitable pair.

## What a positive result does NOT mean

- It does not guarantee the spread will come back, or come back quickly enough to trade.
- It does not mean any profit remains after fees, spread and slippage.
- It does not mean the relationship will survive — relationships break.
- It is not a trading signal.

## Common mistakes

- **Using ordinary ADF critical values on the spread** (the mistake in the worked example).
- **Testing hundreds of pairs and keeping the "winners"** without a multiple-testing correction.
- **Fitting the hedge ratio on all the data, then "testing" on the same data** — look-ahead.

## Quick check

A two-series Engle–Granger test gives a statistic of −3.5. Is that significant at 5%? *(Yes: −3.5 is below −3.34. It is evidence of cointegration in that sample — not proof of a tradable or profitable relationship.)*

## Summary and next step

Cointegration means a combination of two wandering prices is stationary. The Engle–Granger test fits a line, then tests its leftovers with residual-based critical values that are stricter than the ordinary ADF ones. Take the course quiz, then ask the **Research Explainer** about any step you'd like explained again.
