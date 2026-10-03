# Research Evidence Impact Register (2026-10-03)

This register maps every historical research family to the defects confirmed by the 2026-10-03 five-gate audit and remediation batch 1.

- **Nothing was deleted or rewritten.** Each original result stays in its artifact or log; this register only classifies it.
- **Classifications:** CANONICAL / LEGACY_DESCRIPTIVE / SUPERSEDED / INVALIDATED / UNVERIFIED.
- **CRRP reset:** every result in this table predates the CRRP-2026-10-02 reset, so none is current evidence regardless of defects.
- **Holdout:** all history ≤ 2026-09-24 is PSEUDO_HOLDOUT.

| # | Research family | Defects | Recorded result | Classification | Rerun required? | Corrected methodology | Descriptive-only? |
|---|---|---|---|---|---|---|---|
| 1 | Kalman pairs pilot / 10- and 7-pair portfolios | F-03 seed look-ahead, F-05 null, F-07 B, F-12 borrow assumed 0, F-09 last row, P&L-measurement artifact (09-29 audit) | "Edge" passed permutation + leg overlap | **INVALIDATED** (as edge evidence) | Yes, after D-4 and D-8 | Canonical seed (`run_canonical`, L = 1000) → CANONICAL lifecycle → Step-17 null → BH over the registered family → zone-separated selection / validation | Yes |
| 2 | 100-coin cointegration scan | F-03 (downstream P&L), F-10 survivorship, F-16 repeated search, F-09 | 7-pair survivor set | **LEGACY_DESCRIPTIVE** (EG screening math itself is calibrated) | Yes (selection in zone S only) | EG + BH with a cumulative family registry; point-in-time universe (D-6) | Yes |
| 3 | 10-pair CRRP retest (stopped 2026-10-03) | Was running on the legacy seed (F-03) | Incomplete, stopped by owner | **SUPERSEDED** by amendment 2 (not run) | Yes, on owner approval | `pairs_crrp_retest.py` amendment 2 (causal seed, rolled-return controls) | — |
| 4 | Feature leaderboard / IC validation | F-02 IID p, F-04 d = 0 labels, F-07, F-09 | Ranked features with p-values | **LEGACY_DESCRIPTIVE** | Yes, after D-1 and D-2 (not the whole leaderboard; a registered subset) | `TimingContract(d ≥ 1)` labels + calibrated dependence-aware test + family correction | Yes |
| 5 | Crypto cross-sectional ranking (RESEARCH_NEGATIVE ×2) | F-01 PCA look-ahead, F-02, F-04, F-10 | Negative | **LEGACY_DESCRIPTIVE** (negative not overturned, not confirmed) | Optional (low priority) | Causal PCA (D-9), d ≥ 1, point-in-time universe | Yes |
| 6 | Forex cross-sectional top_k = 1 "ECONOMICALLY_SUPPORTED" | F-01, F-02, F-04 (bounce / latency most severe at short horizon), F-07 | Supported for top_k = 1 only | **UNVERIFIED**: the positive claim is withdrawn pending a rerun | **Yes, highest priority** among positives | Same as #5; forex universe survivorship is lower risk but unverified | Yes until rerun |
| 7 | Forex top_k = 3/5/10 sweep | Same as #6 | RESEARCH_NEGATIVE | **LEGACY_DESCRIPTIVE** | No (dominated by #6) | — | Yes |
| 8 | Contagion engine (2,156 pairs, 0 KEEP) | F-04, F-07 (B = 100 ≪ 43,119 needed), F-02 | Negative | **LEGACY_DESCRIPTIVE**; negative stands only as "no detectable effect at this resolution" | Not without a two-stage design (D-3) | Analytic screen → permutation confirmation within the screened family | Yes |
| 9 | DCA / Martingale research (BTC descriptive, calibration) | F-08 statistic does not respond to the effect, M-06 ratios, F-09 | Descriptive P&L; no significance claim | **LEGACY_DESCRIPTIVE**; Sharpe/Sortino derived from the old r_multiple → **INVALIDATED** | Not the full experiment (forbidden); redesign + calibration first (D-5) | Effect at ladder scale, a single MTM statistic, sufficient cycles, re-calibrate | Yes |
| 10 | Walk-forward studies | M-03 boundary overlap, F-03 (legacy seed in folds), F-02 / F-07 for OOS p | OOS summaries | **LEGACY_DESCRIPTIVE** | Yes, once the folds use `run_canonical` | Disjoint folds (fixed) + causal seed per fold | Yes |
| 11 | Research-lab reports ("FDR-adjusted", "SUPPORTED") | F-11 mislabeled family correction | Single-test p shown as FDR-adjusted | **UNVERIFIED** (family claim withdrawn; raw p stands) | No; field corrected for future runs | Verdict tiers (D-7) + family registry (F-16) | Yes |
| 12 | Strategy-scorer rankings (portfolio backtester) | F-14 undefined → 0 | Rankings | **LEGACY_DESCRIPTIVE** | On demand | Undefined ratios unscored (fixed); thresholds to be re-specified | Yes |
| 13 | MT5 / forex pairs real-data scan (0/378) | F-16, F-03 if the Kalman stage was used | 0 survivors | **LEGACY_DESCRIPTIVE** (negative) | No | — | Yes |
| 14 | Inference calibration (new, 2026-10-03) | None known (synthetic, known truth) | Size/power table | **UNVERIFIED** (registered); method choice pending | No | — | Synthetic only, no market claim |

## Summary

**Canonical evidence:** none. No historical family supports a tradeable edge.

**Positive claims, all withdrawn or invalidated:**
- Kalman pairs edge: INVALIDATED.
- Forex top_k = 1: UNVERIFIED.

**Negative results** stay as descriptive negatives at their (often low) resolution.

**Rerun priority, once the owner decisions are made:**
1. Forex top_k = 1 (D-1, D-2).
2. Canonical pairs retest (D-4, D-8).
3. Registered feature subset (D-1, D-2, D-3).
4. DCA redesign (D-5).
