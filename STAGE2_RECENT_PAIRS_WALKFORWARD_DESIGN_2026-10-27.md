# Stage 2 — Recent-window pairs: rolling walk-forward (design, fixed before any result)

*claude code changed: 2026-10-27 — new. Agreed with the owner 2026-10-27 (windows: select on 12 months, trade 1 month;
break rule: rolling cointegration p > 0.10, or spread beyond 3σ for more than 2 half-lives). Written and hashed BEFORE
the first run; the run records this file's SHA-256. Any change after the first run is a new version, not an edit.*

**Status: EXPLORATORY — NOT CLAIM-ELIGIBLE.** Every candle used is pre-seal history, which is pseudo-holdout under
CRRP-2026-10-02 (crypto sealed from 2026-10-04, D-8; forex sealed from 2026-10-16 08:00, QC-2/v7). Stage 2 can show
whether the rule deserves a forward test; only data after the seals can ever support a claim.

## Question

If at the start of each month we select the pairs that were cointegrated over the previous 12 months, does the
relationship (a) persist and (b) make money after costs over the following month?

## Schedule

- Rebalance at 00:00 UTC on the 1st of each month `t`. Selection window `[t − 12 months, t)`, trading month `[t, t + 1 month)`.
- Crypto: first trading month 2022-10 (data start 2021-09-26), last 2026-09. Forex: first 2021-11 (data start
  2020-10-07), last 2026-09. Only whole months; nothing at or after either seal is read.

## Universe (decided per month, from the selection window only)

- Crypto: coins with a Binance perpetual (funding data present) and ≥ 95 % hourly coverage in the selection window;
  the 50 largest by median daily USDT volume in the selection window. **Limitation:** the coin list is today's files —
  coins delisted before today are missing (survivorship bias, which flatters results).
- Forex: every pair in `data/forex/*_1h.csv` with ≥ 95 % of the median pair's trading candles in the selection window;
  pairs tied only through a currency peg (DKK~EUR, HKD~USD) are excluded (Stage 1 rule).

## Selection (per month, per asset class)

1. Every unordered pair, A = alphabetically first, log prices, A regressed on B with an intercept.
2. Engle–Granger (statsmodels `coint`, trend "c") with a **fixed lag order** `⌊12 (n/100)^¼⌋` (Schwert). Stage 1 used
   the AIC lag search; a fixed order is 10× faster and is declared here (BTC/ETH last 12 months: p 0.12 fixed vs 0.09 AIC).
3. Benjamini–Hochberg across all testable pairs of that month and class, **q = 0.05**.
4. Half-life of the selection-window residual (exact AR(1), gaps never bridged) within **2–120 h**.
5. Survivors are traded. No cap on the number of pairs.

## Primary outcome 1 — persistence (cost-free)

For every selected pair: ADF (constant, Schwert lag) on the trading-month residual built with the **frozen**
selection coefficients (ordinary tables are valid: nothing is estimated on that month). Persisted = p < 0.05.
Reported against the same rate for **non-selected testable pairs** of the same month (baseline).

## Primary outcome 2 — trading, two pre-declared variants

Signal on an hourly close, fill at the **next** hourly close (one-bar delay; no look-ahead).

- **Variant S (static):** spread = log A − α̂ − β̂ log B with the selection OLS α̂, β̂; z = (spread − mean) / sd of the
  selection-window residual.
- **Variant K (Kalman):** state (α, β) as a random walk, observation log A = α + β log B + ε. Q = δ/(1 − δ)·I with
  δ = 1e-4, R = variance of the selection-window OLS residual, initial state = selection OLS α̂, β̂, P₀ = 0. The signal
  is the one-step-ahead prediction error standardised by its predicted standard deviation (state at t − 1 only).
- Entry: z ≥ +2 → short the spread (short A, long β·B); z ≤ −2 → long the spread. Exit: z crosses 0, or the month
  ends (forced close at the last hourly close of the month), or the break rule fires. One position per pair at a time.
- Hedge ratio at entry is frozen for the life of the position (variant S: β̂; variant K: the filtered β at the signal
  candle). Sizing: $1,000 on leg A, $1,000 × |β| on leg B (signed by β).
- **Break rule (agreed):** (i) every 168 h from the month start, Engle–Granger (fixed lag) on the trailing 12 months
  ending at that hour; p > 0.10 → close and drop the pair for the rest of the month. (ii) |z_static| > 3 on every
  candle for more than 2 × half-life hours → close and drop. Both rules use the static (selection) relationship in
  both variants, so the Kalman cannot hide a broken relationship by adapting to it.

## Costs (all charged; results shown gross and net)

- Crypto (both legs on USDⓈ-M perps; spot candles stand in for perp prices): taker fee 0.05 % per side per leg
  (`PERP_TAKER_FEE_RATE`), slippage 5 bp per side per leg (flat, conservative for the top-50), real historical funding
  every 8 h from `data/*_USDT_funding.csv` (long pays positive rates, short receives).
- Forex: half the pair's mean 30-day H1 spread from MT5 per side per leg, plus swap at the pair's current MT5
  long/short rates charged per calendar day held (snapshot `research_data/recent_pairs_wf/fx_costs_snapshot.json`,
  GlobalDynamicMarkets-Demo). **Limitation:** today's spreads and swap rates applied to 2021–2026; no commission
  (owner: none on this account).
- Sensitivity: net results also at 2× costs.

## Tests and reporting

- Unit of inference: the **month** (non-overlapping). Monthly net P&L per variant per class: one-sample t-test and
  sign test of mean > 0. Four primary tests (2 variants × 2 classes): Bonferroni α = 0.0125.
- Also reported, descriptive: trades, win rate, P&L per trade, worst month, drawdown in $ and %, pairs selected per
  month, how often each break rule fired, Kalman β drift.
- Nothing here may change a live setting, a sealed holdout or another registered protocol.

## Addendum A1 — 2026-10-27, before any forex result (crypto run had not completed either: it failed at load)

- **Forex universe:** only pairs the current MT5 broker (GlobalDynamicMarkets-Demo) offers — 46 of the 58 files. The
  other 12 (AUD/SGD, CAD/CHF, CHF/SGD, EUR/DKK, EUR/HKD, EUR/PLN, GBP/DKK, NOK/JPY, NOK/SEK, SEK/JPY, SGD/JPY,
  USD/THB) have no spread or swap figures and cannot be traded at this broker.
- **Forex spread:** the mean of the last **7 days** of H1 bar spreads (not 30 days: a 30-day history request stalled
  the MT5 terminal). MT5 stores the lowest spread in each bar, so this understates the cost — the 2× cost
  sensitivity covers it.
