# From IC to a Research Verdict

You now have every piece needed to understand why a feature earns a real research verdict instead of a gut feeling. Put together, the pipeline this platform actually runs looks like this:

```
FEATURE
   ↓
IC / SIGNIFICANCE TEST        <- is the relationship distinguishable from noise?
   ↓
MULTIPLE-TESTING CORRECTION   <- corrected against every test actually run, not a subset
   ↓
DEPENDENCE-AWARE TESTING      <- accounts for overlapping/autocorrelated labels
   ↓
ECONOMIC SIGNIFICANCE         <- is the effect big enough to survive real costs?
   ↓
TEMPORAL STABILITY            <- does it still work recently, or has it decayed?
   ↓
VERDICT: STRONG KEEP / KEEP / REVIEW / DELETE
```

A feature only becomes eligible to power a live strategy if it clears **every** stage. Missing evidence at any stage is treated the same as failing it — never as a pass by default. This platform calls that principle *fail closed*: unknown is not the same as validated.

## Why this matters beyond the math

Two real strategies exist in this platform's codebase: one built on RSI/Bollinger Band mean-reversion, and one built on EMA-trend/structure-following logic. Neither is currently allowed to trade with real capital, because neither has cleared this full pipeline — the RSI-based one failed on economic significance and (after the fixes in the previous lesson) statistical significance too; the EMA-based one has simply never been run through the pipeline at all, and this platform treats *never tested* identically to *tested and rejected*, not as a free pass.

That is the discipline this whole course path is building toward: **a strategy should never become production-eligible merely because it looked profitable in a backtest.** A backtest is a hypothesis test, not proof. Everything from here forward in this learning path — multiple testing, walk-forward validation, permutation testing, feature decay — exists to keep that hypothesis honest before real money is ever at risk.

**Test yourself (Research Decision):** A newly-discovered feature passes IC testing, survives family-wide multiple-testing correction, and clears the economic-significance bar — but has never been checked for temporal stability. Would you mark it KEEP, REVIEW, or REJECT?
