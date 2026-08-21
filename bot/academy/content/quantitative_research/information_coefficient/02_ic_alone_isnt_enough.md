# Why IC Alone Isn't Enough

Lesson 1 showed RSI producing a statistically real, positive IC on every one of 20 tracked crypto symbols. If you stopped there, you might conclude RSI is a tradable edge. It is exactly this kind of stopping-early that this course exists to prevent.

## Statistical significance is not economic significance

A feature can be **statistically real** — its correlation with returns is unlikely to be pure chance — while still being **economically useless**, because the actual size of the effect is too small to survive real trading costs (fees, slippage, spread).

Steph Quant Technologies' research pipeline checks both, separately, on purpose:

- `passed_statistical_significance` — is the relationship distinguishable from noise?
- `passed_economic_significance` — is the *size* of the relationship big enough to matter, after costs?

Here is what actually happened when RSI was tested this way, using this platform's real research data:

- RSI's quartile spread (the return difference between the top and bottom quartile of RSI readings) ranged from **0.0000129 to 0.00122** across all 20 symbols.
- The platform's economic-significance threshold is **0.005** (0.5%) — the minimum spread considered large enough to plausibly cover trading costs.
- **Every single symbol's spread fell below that threshold.**

RSI's predictive power is real. It is also, on this data, roughly 4-40x too small to trade profitably once you account for the cost of actually executing trades.

## The actual verdict this produced

Steph Quant Technologies' `validated_feature_registry.py` records exactly this outcome for the strategy that relies on RSI:

> *"rsi: statistically real (20/20 symbols pass real FDR-corrected significance) but economically negligible everywhere — quartile spread 0.0000129-0.00122 vs. the 0.5% economic-significance threshold... REVIEW on 20/20 symbols, KEEP on 0/20."*

The strategy built on this feature is marked `production_eligible = False`. Not because the research was sloppy — because the research was done properly, and properly-done research said no.

**This is the single most important habit this entire course is trying to build**: a positive result is the *start* of an investigation, never the end of one.

**Test yourself:** A feature shows IC = 0.02 with p < 0.01 across 40,000 observations, but its quartile return spread is 0.0008 against a 0.5% economic-significance threshold. Should it be marked production-eligible?


<!-- claude code changed: rebrand from Quant Bot Pro to Steph Quant Technologies -->
