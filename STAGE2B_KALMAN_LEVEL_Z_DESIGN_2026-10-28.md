# Stage 2b — Kalman level-z variant of the recent-window pairs walk-forward (design v2, fixed before any result)

*claude code changed: 2026-10-28 — new. Owner asked 2026-10-28 to "go ahead with the Kalman level-z design"; the
choices below were delegated and are the strictest defensible ones. Written and hashed BEFORE the first run; the run
records this file's SHA-256. Any change after the first run is a new version, not an edit.*

**Status: EXPLORATORY — NOT CLAIM-ELIGIBLE.** Same pre-seal data as Stage 2 (CRRP-2026-10-02). In addition this version
was designed **after** the Stage 2 results were seen (crypto S borderline, Kalman K degenerate), so it is not blind.
Only a forward test on post-seal data could ever support a claim.

## Why a new version

Stage 2 variant K standardised the one-step-ahead prediction error. With δ = 1e-4 fixed independently of the residual
variance R (≈ 1e-4 in log space), the intercept's random walk dominated, the filter tracked the spread level, and the
innovation z almost never reached 2 (4 crypto trades, 0 forex). That signal is not a stretched-spread signal. This
version keeps every Stage 2 rule except the Kalman signal.

## Everything inherited unchanged from STAGE2_RECENT_PAIRS_WALKFORWARD_DESIGN_2026-10-27.md (incl. addendum A1)

Schedule, universe, selection (EG fixed lag, BH q = 0.05, half-life 2–120 h), entry |z| ≥ 2, exit on z crossing 0 /
month end / break, one-bar fill delay, frozen hedge ratio at entry, $1,000 on leg A and $1,000 × |β| on leg B, both
break rules (on the static relationship), all costs, 2× cost sensitivity, month as the unit of inference.

## Selection is reused, not recomputed

The monthly selected pairs are read from the Stage 2 result `research_data/recent_pairs_wf/stage2_20261027T195720Z.json`
(its SHA-256 is recorded). For each selected pair the selection-window Engle–Granger regression is **re-derived from the
CSVs** (not taken from the file); the run stops if the recomputed p-value differs from the recorded one by more than
1e-9. Variant S is re-run as a **reproduction check**: its trades must equal Stage 2's S trades for that month (pair,
entry, exit, direction, net to 1e-6), or the run stops.

## Variant L (Kalman, level z)

- Demean with the selection-window means: y = log A − m_A, x = log B − m_B (m_A, m_B frozen). With β = β̂ this is
  exactly the static spread log A − α̂ − β̂ log B (OLS: α̂ = m_A − β̂ m_B).
- State: β only, random walk β_t = β_{t−1} + w, w ~ N(0, Q). Observation y_t = β_t x_t + ε, ε ~ N(0, R).
  The intercept is **not** a state, so the filter cannot absorb a level deviation into it.
- R = variance of the selection-window OLS residual. Initial state β̂, P₀ = 0, started at the first trading-month candle.
- **Q per pair:** Q = R · g² / Var_sel(x) with g = 1 / (10 × half-life in hours). For a local-level filter the
  steady-state gain is ≈ √(Q x² / R) ≈ g, i.e. the fitted hedge adapts about ten times slower than the spread
  mean-reverts, so roughly a tenth of a typical deviation is absorbed before it reverts. Fixed here, never tuned.
- Spread s_t = y_t − β_{t|t−1} x_t, using the prior (information to t − 1 only); z_L = (s_t − μ) / σ with the static
  selection-window residual mean μ and sd σ — a **level** z on the same scale as variant S.
- Hedge ratio frozen at entry = β_{t|t−1} at the signal candle.

## Tests and reporting

- Primary: variant L monthly net P&L, crypto and forex, one-sample t-test and sign test of mean > 0.
- The test family on this data is now 6 (Stage 2's four + these two): **Bonferroni α = 0.05 / 6 ≈ 0.0083**.
- Descriptive: trades, win rate, P&L per trade, exit reasons, drawdown in $ and %, gross / net / 2× costs,
  β drift at month end, share of candles with |z_L| ≥ 2 vs |z_S| ≥ 2 (shows whether the signal is still absorbed).
- Nothing here may change a live setting, a sealed holdout or another registered protocol.
