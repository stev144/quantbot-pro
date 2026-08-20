# Multiple Testing and Autocorrelation

Two more things can make a feature look better than it really is, and both are subtle enough that experienced people miss them.

## Multiple testing: the "20 coin flips" problem

If you flip 20 fair coins, it's not surprising if one of them lands heads 8 times in a row — you were bound to see *something* unusual out of 20 tries. The same thing happens in research: if you test 16 features across 20 symbols — 320 individual tests — a handful will look "statistically significant" purely by chance, even if none of them have any real predictive power.

The fix is a **multiple-testing correction** (this platform uses Benjamini-Hochberg FDR correction) applied across the *entire* set of tests actually run — not just a handful of them. This matters more than it sounds: this platform's own research pipeline had a real bug where correction was applied separately to each symbol's ~16 features (20 independent corrections, each blind to the other 19 symbols) instead of across the full 320-test family. Fixing the scope of that correction — not the math, just *how many tests it was actually corrected against* — is by itself enough to change which features pass.

## Autocorrelation: forward-looking labels overlap

A "4-hour forward return" label on hourly candles is not one independent data point per row. Row *t*'s label and row *t+1*'s label share 3 of their 4 underlying hours. Consecutive labels are seriously correlated with each other — which quietly violates a key assumption behind standard significance tests like the t-test and Mann-Whitney U test: that observations are independent.

When that assumption is violated, those tests report p-values that are **systematically too small** — making a feature look more significant than it actually is.

## What happens when both are fixed properly

This platform's research pipeline was corrected to (1) pool the multiple-testing correction across the true 320-test family, and (2) replace the naive significance test with a **block-permutation test** — which shuffles the data in contiguous blocks (preserving short-range structure) rather than assuming independence, and measures how often *randomly shuffled* data produces a result as strong as the real one.

The real result, re-run on fresh data after both fixes: **0 out of 320 (symbol, feature) combinations passed.** Every one came back `DELETE`. This is not a bug — it is exactly what the fix was supposed to reveal: the earlier "REVIEW on 20/20 symbols" result was itself partly an artifact of an under-corrected, autocorrelation-blind test.

**Test yourself:** True or False — a feature with p < 0.05 from a standard t-test on overlapping forward-return labels has definitely proven a real, tradable relationship.
