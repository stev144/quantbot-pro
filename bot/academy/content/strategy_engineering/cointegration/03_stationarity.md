# Stationarity: Series That Come Back

**In this lesson you will learn:** what stationary and non-stationary series are, why most prices are non-stationary, and how stationarity is tested.

## Why it matters

Whether a spread is useful for research depends on one property: does it keep returning to its usual level, or can it wander off anywhere? That property is called **stationarity**, and it is the heart of the next lesson.

## The idea in plain words

A **stationary** series keeps the same typical level and the same typical wobble over time. It may move around, but it keeps coming back.

A **non-stationary** series has no level it must return to. It can drift anywhere, and the longer you wait, the further from its starting point it can be. Most prices behave like this.

## An everyday example

The temperature in a room with a thermostat is stationary: it wobbles a little — 20.5°, 19.8°, 20.2°, 19.6°, 20.4° — but always comes back to about **20.1°** (the average of those five readings), because the thermostat pulls it back.

A person taking a random walk — one step left or right each minute, decided by a coin flip — is non-stationary. Nothing pulls them back to where they started.

## A worked example (illustration)

In a random walk where each step is +1 or −1, the typical distance from the start grows with the square root of the number of steps:

- after **100** steps, the typical distance is about √100 = **10** steps;
- after **400** steps, about √400 = **20** steps.

The spread of possible positions keeps growing — so the series has no fixed "usual level". Compare the thermostat, whose typical wobble stays about the same no matter how long you watch.

## The mathematics

A series y is (weakly) stationary when its mean and variance do not change over time, and the relationship between y_t and y_{t−k} depends only on the gap k:

E[y_t] = μ, Var(y_t) = σ², Cov(y_t, y_{t−k}) depends only on k.

The standard non-stationary example is the **random walk**: y_t = y_{t−1} + ε_t, whose variance after t steps is t · σ² — it grows without limit.

## How stationarity is tested

The **Augmented Dickey–Fuller (ADF) test** asks: is there evidence against a "unit root" (non-stationary behaviour)? Its **null hypothesis** is that the series is non-stationary. A strongly negative test statistic is evidence against that null.

Two cautions:

- **Failing to reject is not proof** of non-stationarity — the test has low power on short samples.
- **No test can prove stationarity.** It can only find evidence against non-stationarity in the data you have.

## How Steph Quant Technologies uses it

Our pairs research uses ADF-type tests on spreads. Lesson 4 explains a crucial detail: when the spread was built from an *estimated* hedge ratio, the ordinary ADF critical values are the wrong ones.

## Common mistakes

- **Treating a price as stationary because it "looks range-bound" on a short chart.** Most prices are not.
- **Reading "not rejected" as "proven non-stationary".**
- **Assuming a series that was stationary last year still is** — structural breaks happen.

## Quick check

A spread has averaged about zero for two years with a similar wobble every month. Is it proven stationary? *(No — that is evidence consistent with stationarity, not proof; it can still break in the future.)*

## Summary and next step

Stationary series keep coming back to a stable level; non-stationary series, like most prices, can drift anywhere. Next: **cointegration** — when two non-stationary prices combine into a stationary spread.
