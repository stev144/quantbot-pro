# Two Price Series and Correlation

**In this lesson you will learn:** what a price series is, what correlation measures, and why we measure it on *returns* rather than on prices.

## Why it matters

Pairs research starts with a simple question: do two assets have something to do with each other? The first tool most people reach for is correlation. It is useful — but it is also the tool beginners most often misread, and the rest of this course depends on understanding exactly what it does and does not tell you.

## The idea in plain words

A **price series** is just a list of prices in time order — for example, the closing price of a coin at the end of every hour.

A **return** is the percentage change from one price to the next. If a price goes from 100 to 102, the return is +2%.

**Correlation** measures how consistently two series move in the same direction *at the same time*. It is a number between −1 and +1:

- **+1** — they always move together, in a straight-line way.
- **0** — no straight-line relationship.
- **−1** — they always move in opposite directions.

## An everyday example

Think of ice-cream sales and sunglasses sales in a beach town. On sunny days both go up; on rainy days both go down. Their daily changes are strongly correlated. But neither causes the other — the weather drives both. Correlation tells you they *move together*; it does not tell you *why*.

## A worked example (illustration, not market data)

Two hypothetical assets, A and B, have these daily returns over five days:

| Day | A | B |
|---|---|---|
| 1 | +1% | +2% |
| 2 | −2% | −1% |
| 3 | +3% | +2% |
| 4 | 0% | −1% |
| 5 | +2% | +3% |

1. Average return: A = (1 − 2 + 3 + 0 + 2) / 5 = **0.8%**; B = (2 − 1 + 2 − 1 + 3) / 5 = **1.0%**.
2. Each day's distance from the average — A: 0.2, −2.8, 2.2, −0.8, 1.2; B: 1, −2, 1, −2, 2.
3. Multiply the pairs and add them: 0.2 + 5.6 + 2.2 + 1.6 + 2.4 = **12.0**.
4. Square each list and add: A gives **14.8**, B gives **14.0**.
5. Correlation = 12.0 / √(14.8 × 14.0) = 12.0 / 14.394 ≈ **0.834**.

So A and B tend to move in the same direction on the same days — a fairly strong positive correlation.

## The mathematics

For two series X and Y:

ρ = cov(X, Y) / (sd(X) · sd(Y))

and the sample version is Σ(xᵢ − x̄)(yᵢ − ȳ) / √( Σ(xᵢ − x̄)² · Σ(yᵢ − ȳ)² ) — exactly the steps above.

## Why returns, not prices

Most prices *trend*: they drift up or down over long periods. Two completely unrelated prices that both happen to drift upwards will look "correlated" if you compare the price levels directly. Comparing **returns** removes most of that trap, because returns do not drift the way price levels do. Our platform's correlation figures (for example the co-movement measure on the home page) are computed on returns for this reason.

## How Steph Quant Technologies uses it

Correlation is used to *describe* co-movement and diversification — how much a group of coins tends to move together. It is not used on its own to choose trades.

## Common mistakes

- **Correlation is not causation.** Two assets can move together because of a shared third cause.
- **Correlation changes.** In market crashes, correlations often jump towards +1 just when diversification is needed most.
- **High return correlation does not mean the prices stay close together.** That is a different property — and it is exactly what the rest of this course is about.

## Quick check

If two coins both rose steadily all year, can you conclude their prices are related? *(No — trending prices can look related by chance. You would at least compare their returns, and even then correlation would not tell you whether their prices stay close.)*

## Summary and next step

Correlation measures whether two series move together at the same time, on a −1 to +1 scale, and should be measured on returns. Next: the **spread** — a way to watch whether two prices stay close together.
