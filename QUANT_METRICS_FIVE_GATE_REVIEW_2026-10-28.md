# Institutional market metrics: five-gate review (2026-10-28)

*claude code changed: 2026-10-28 — new. Owner request: replace the retail indicators on the Market Regime cards (ADX,
ATR×, EMA, BB) with institutional quantitative factors, after a five-gate review (G1 software, G2 trading relevance,
G3 maths, G4 statistics, G5 research integrity). Code: `bot/quant_metrics/`. Tests: `bot/tests/test_quant_metrics.py`.*

**Status: DESCRIPTIVE.** No metric here is a validated trading signal (CRRP-2026-10-02). Ranks describe today's listed
universe only (survivorship, D-6). The `RegimeDetector` label on each card is unchanged; only the four indicator
readouts were replaced.

## Verdicts

| # | Requested metric | Verdict | Gate that decided |
|---|---|---|---|
| 1 | Rolling z-score | BUILT, descriptive | G4: price is non-stationary, so z is a distance, never a probability |
| 2 | Momentum rank | BUILT | — |
| 3 | Volatility regime | BUILT | G3: classes are equal-probability quintiles, not hand-picked cut-offs |
| 4 | Hurst exponent | BUILT as the variance-ratio Hurst + Lo–MacKinlay test | G4: fixed 0.45/0.55 bands sit inside the estimator's noise |
| 5 | Sharpe 30 / 90 | BUILT with standard error | G4: a 30-day Sharpe has SE ≈ 3.5 — shown, never hidden |
| 6 | Trend quality | BUILT as one statistic: 100·Φ(Newey–West t) | G3: no arbitrary weights |
| 7 | Efficiency ratio | BUILT | — |
| 8 | Mean-reversion score (0–100) | REJECTED → descriptive flag | G3/G4: a "probability" needs calibration that does not exist |
| 9 | Relative strength rank | REJECTED | G5: identical to momentum rank by definition |
| 10 | Liquidity score | BUILT for crypto (Amihud); forex function only | G2/G4: spread/depth/trade-count unavailable for 161 symbols; FX tick volume is not dollars |
| 11 | Regime confidence | REJECTED | G3: factor "agreement" voting is an arbitrary weighting; RegimeDetector already reports its own agreement |
| 12 | SQ composite score | REJECTED until validated | G3/G5: weights without measured predictive power would advertise an edge the evidence does not support |

## Built metrics

Windows are calendar units (hour / day / week / month / year), chosen before looking at any result.

**1. Rolling z-score** — `factors.rolling_zscore`
- Definition: z = (ln Pₜ − mean(ln P, last 168 h)) / std(ln P, last 168 h, ddof 1).
- Rationale: distance from the recent equilibrium in volatility units, comparable across assets (log price).
- Statistics: price is not stationary, so no tail probability is implied; labels are SD units (WEAK < 1 ≤ MODERATE < 2 ≤ STRONG; EXTENDED_ABOVE/BELOW at ±2).
- Complexity O(w). Edge cases: < 168 candles or zero dispersion → None.
- Display `ZS +2.14`.

**2. Momentum rank** — `factors.momentum_return` + `ranking.cross_sectional_percentile`
- Definition: 30-day log return; percentile 100·(rank − 1)/(N − 1), average ranks for ties, within one asset class at one instant.
- Rationale: cross-sectional momentum (Jegadeesh–Titman; time-series momentum in CTA practice).
- Statistics: truly cross-sectional; survivorship-limited universe (D-6), so no historical claim.
- Complexity O(N log N). Display `MOM 92`.

**3. Volatility regime** — `factors.realized_vol_series` + `ranking.time_series_percentile` + `regimes.volatility_regime`
- Definition: σ = std(daily log returns, 20 d) · √A · 100; percentile of today's σ within its own trailing 365 days; quintile classes VERY_LOW … EXTREME.
- A = 365 (crypto, trades daily), 260 (forex). Returns, never price levels. Needs ≥ 120 days of the σ series.
- Display `VOL HIGH` (tooltip: σ and percentile).

**4. Variance-ratio Hurst** — `statistics.lo_mackinlay_variance_ratio` + `regimes.variance_ratio_regime`
- Definition: VR(q) = Var(q-period return) / (q · Var(1-period return)) with Lo–MacKinlay (1988) bias corrections; H = 0.5 + ln VR / (2 ln q); q = 24 on hourly data (day vs hour).
- Regime: TRENDING if z* > 1.96, MEAN_REVERTING if z* < −1.96, else RANDOM_WALK. z* is heteroskedasticity-robust.
- Why not R/S Hurst with 0.45/0.55 bands: on ~1,000 points the R/S estimate's sampling spread is wider than the band, so the bands would mostly classify noise.
- Requires T ≥ 10q. Display `H 0.47 RW`.

**5. Sharpe 30 / 90** — `statistics.sharpe_ratio`
- Definition: SR = (mean(r) − rf/A)/std(r) · √A on daily simple returns; SE = √((1 + SR_p²/2)/n) · √A (Lo 2002, IID); rf parameter, default 0.
- Statistics: with n = 90 the SE is about 2.0, so most SR90 values are indistinguishable from zero. The SE is always displayed.
- Display `SR90 1.2±2.0`.

**6. Trend quality** — `factors.trend_quality`
- Definition: t = mean(hourly log return, 168 h) / SE_NW; TQ = 100 · Φ(t); Newey–West lag ⌊4(n/100)^{2/9}⌋.
- One statistic carries slope (mean), volatility adjustment (SE), consistency, and persistence (NW widens the SE under autocorrelation). No weights.
- 50 = no drift; > 97.5 or < 2.5 = significant at 5 %. Correlated with Sharpe by construction: Sharpe is magnitude, TQ is confidence.
- Display `TQ 71`.

**7. Efficiency ratio** — `factors.efficiency_ratio`
- Definition: |Pₜ − Pₜ₋₂₄| / Σ|ΔP| over 24 h ∈ [0, 1] (Kaufman).
- A flat path → None. Display `ER 0.34`.

**10. Liquidity (crypto)** — `factors.amihud_illiquidity` + rank
- Definition: Amihud (2002) ILLIQ = mean(|r_d| / quote volume_d) over 30 days, as % move per $1M traded; score = 100 − cross-sectional percentile (100 = most liquid).
- Forex: `factors.fx_spread_cost_ratio` (spread ÷ one typical hourly move) exists for the forex pages; it is not on this crypto-only page.
- Display `LIQ 85`.

**8 (replacement). Mean-reversion setup flag**
- |z| ≥ 2 AND the variance-ratio test rejects a random walk toward mean reversion. Boolean, descriptive, not a probability.

## What would let a rejected metric in

- **SQ score:** pre-register a composite (equal weights or weights fixed in writing), then test its rank IC against forward returns in zone S with block permutation (D-2/D-10, B = 999). Confirm in zone V. Report it on the D-7 ladder.
- **Mean-reversion probability:** calibrate P(revert within h | z, VR regime, vol regime) on zone S, then check calibration (Brier score, reliability curve) on zone V.
- **Regime confidence:** a proper posterior from a fitted model (e.g. a hidden-Markov model) with out-of-sample log-likelihood, not a vote count.

## Data and cost on the page

- /market/ fetches 1,000 hourly candles (RegimeDetector still uses the last 300) and 400 daily candles per symbol.
- The overview is cached for the kline cache's own 5 minutes. The home page snapshot is unchanged (`with_metrics=False`).
- The still-forming candle is dropped before any metric is computed (data_fetcher F-09, and `api.completed()`).
