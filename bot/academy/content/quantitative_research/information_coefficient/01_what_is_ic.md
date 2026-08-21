# What Is the Information Coefficient?

The **Information Coefficient (IC)** measures how well a feature predicts future returns. It's the Spearman rank correlation between a feature's value at time *t* and the return that actually happens afterward.

- **IC near 0** — the feature tells you nothing about what happens next.
- **IC near +1** — the feature almost perfectly predicts the direction and rank of future returns.
- **IC near -1** — the feature is inversely related: high feature values precede *negative* returns.

In practice, real, useful features in liquid markets usually have IC values that look small in absolute terms — often between 0.02 and 0.10. This surprises people coming from other fields, where a correlation of 0.05 sounds like noise. In quantitative trading, it can be a genuine, tradable edge — **if** it survives everything covered in the rest of this course.

## Why rank correlation, not plain correlation?

Spearman's correlation compares the *ranking* of feature values against the *ranking* of subsequent returns, rather than comparing raw values. This matters because:

1. It's robust to outliers — one enormous return doesn't distort the whole calculation the way it would with a plain (Pearson) correlation.
2. It doesn't assume a straight-line relationship — a feature can be genuinely useful even if its relationship with returns is nonlinear, as long as the *ranking* holds.

## A real example from this platform's own research pipeline

Steph Quant Technologies' `feature_validator.py` computes IC exactly this way — Spearman correlation between a feature column and a forward-return label column, on real historical data across 20 tracked crypto symbols.

Here is a real, current finding from that pipeline for the RSI indicator, tested against every symbol:

| Symbol | IC (Information Coefficient) |
|---|---|
| BTC/USDT | 0.034 |
| ETH/USDT | 0.041 |
| SOL/USDT | 0.061 |
| ... (17 more symbols) | ranging 0.021 to 0.061 |

Every single symbol shows a small but *positive and statistically real* IC for RSI. That might look like the beginning of a strategy. It is not — not yet. The next three lessons explain exactly why, using this same real dataset.

**Test yourself:** if a feature has IC = 0.15 measured on 40,000 historical trades, would you call that IC "acceptable," "good," or "excellent" by the thresholds this platform's own research code uses (`IC_ACCEPTABLE = 0.05`, `IC_GOOD = 0.10`, `IC_EXCELLENT = 0.15`)?


<!-- claude code changed: rebrand from Quant Bot Pro to Steph Quant Technologies -->
