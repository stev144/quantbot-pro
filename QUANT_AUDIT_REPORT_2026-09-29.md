# Quant Bot Pro — Four-Level Quantitative Audit (2026-09-29)

**Scope:** repository-wide audit at four levels: programmatic, architectural, mathematical/statistical, and quantitative/trading-model.
**Mode:** report only. No production code was modified. Independent verification was done with throwaway probe scripts outside the repo (reproduced in Appendix A).
**Priority rule:** audit effort was weighted toward modules that can change the conclusion "this strategy has an edge" or that touch live capital.

---

## 1. Executive Summary

**The single most important finding: the pairs-trading "edge", which is the only strategy currently wired for live margin capital, is an artifact of how P&L is measured.**

The research engine (`entry_exit_engine.py`) books each trade's P&L as the change in the Kalman filter's *innovation* (`spread`) between entry and exit. That quantity is recomputed at every candle with *re-estimated* α and β. It is not the P&L of the fixed quantities a real trade holds. The filter's own adaptation mechanically pulls the innovation back toward zero, so "entering at |z| > 2 and exiting later" looks profitable **on any pair of price series, including two independent random walks.**

Verified by driving the real, unmodified live `PairSignalEngine` (4h time stop, 1h decision lag):

| Data | Research-formula P&L / trade | Research win rate | **True fixed-leg P&L / trade** | **True win rate** |
|---|---|---|---|---|
| Independent random walks, seed 0 | +1.40% | 81.0% | **−0.19%** | 45.8% |
| Independent random walks, seed 1 | +1.47% | 81.2% | **−0.23%** | 45.3% |
| Independent random walks, seed 2 | +1.34% | 78.1% | **−0.34%** | 42.1% |
| Independent random walks, seed 3 | +1.19% | 73.7% | **−0.47%** | 40.5% |
| Independent random walks, seed 4 | +1.37% | 76.1% | **−0.24%** | 45.6% |
| **Real DODO/FIDA** (1h, full history) | +1.13% | 74.6% | **−0.26%** | 45.6% |
| **Real MINA/ONG** (1h, full history) | +0.99% | 74.0% | **−0.35%** | 42.0% |

(True P&L includes the same 0.3% round-trip cost the research engine uses.) The real pairs behave like the random walks: gross true edge ≈ 0, net ≈ −cost.

The same artifact contaminates every piece of evidence stacked on top:

* **Feature IC.** The headline "IC = 0.52, STRONG KEEP" is `IC(−z, spread_{t+4} − spread_t)`. On independent random walks the same computation gives **IC = 0.39 / 0.43 / 0.40** (3 seeds). The IC is mostly mechanical.
* **Permutation test.** It shuffles the z-score/decision columns but keeps the real `spread` column. The null it rejects ("z-score timing is unrelated to Δspread") is false *by construction* for any innovation series, so it cannot detect this artifact. Both live pairs sit at the p-value floor (0.0099 with 100 permutations), with win rates of 87% and 83% and Sharpe ≈ 6.7. Those numbers are economically implausible for hourly crypto stat-arb and consistent with the artifact.
* **Live sizing.** The live position sizer reads its win rate from those permutation verdicts (0.87 and 0.83). It feeds them into a Kelly formula that ignores the payoff ratio. Those verdicts were produced under a different exit configuration (avg hold 33.7h) from the live one (4h time stop). Net effect: near-maximum bets on a negative-expectancy trade.

**Live-capital status:** `pairs_bot_runner.py` has `DRY_RUN = True` and requires a second env-var confirmation to go live, so no real money is currently exposed by this code path. **The pilot must not be switched live.**

Beyond the artifact, the pairs execution layer has a failure mode that would orphan real margin positions. Common exchange errors (insufficient funds, a borrow refused, an invalid order) aren't caught, which leaves a naked leg. The signal engine then believes a trade is open that execution never opened, or the reverse. The pilot's drawdown kill switch is computed but never consulted.

Other material findings:
* Cointegration significance uses Dickey-Fuller p-values on estimated residuals instead of Engle-Granger critical values, so it is anti-conservative.
* The DCA simulator understates crash exposure by about 5× in a one-candle crash, and mislabels that bias as "conservative".
* DCA volatility scaling leaks full-sample information into OOS.
* Sharpe and Sortino are non-standard and inflatable. The Sortino denominator gives 5.0 where the textbook gives 0.70 on the same data.
* Permutation p-value floors combined with family-wide Benjamini-Hochberg (BH) make several feature-research families structurally unable to pass. Their negative verdicts are therefore uninformative rather than evidence of no edge.

**Bottom line for the four levels:**

| Level | Pairs pilot | Single-symbol bot/backtester | DCA engine | Feature research |
|---|---|---|---|---|
| Programmatic | Mostly sound; exception-handling gaps (C-04) | Sound | Sound | Sound |
| Architectural | Signal/execution state desync (C-04, M-07) | Live uses forming candle (H-07) | OOS fold boundaries force mark-to-market (MTM) closes (H-10) | Sound |
| Mathematical/statistical | **Invalid P&L, IC and permutation null (C-01 to C-03)**; cointegration p-values anti-conservative (H-03); Kelly wrong (H-01) | Sharpe/Sortino non-standard (H-11) | Vol-scale leakage (H-09); permutation null weak (M-05) | p-floor vs BH makes results uninterpretable (H-12) |
| Quantitative/trading-model | **No demonstrated edge; true expectancy ≈ −cost** | Gap fills optimistic (M-01) | Tail exposure understated (H-08) | — |

---

## 2. Repository Inventory (condensed)

81,106 lines of Python in 260+ files (migrations excluded). Grouped by package, with risk class and influence.

| Package / module group | Lines (approx) | Purpose | Influences | Risk |
|---|---|---|---|---|
| `bot/pairs/*` (config, kalman_online, signal_engine, monitor_engine, health, execution, margin_adapter, trade_logger) | 1.9k | Live pairs pilot: online Kalman, decisions, isolated-margin execution | Signals, sizing, execution, live capital | **CRITICAL** |
| `bot/core/pairs_bot_runner.py` | 0.5k | Pairs live loop | Execution timing, live capital | **CRITICAL** |
| `bot/research/entry_exit_engine.py` | 2.9k | Pairs entry/exit rules, Kelly sizer, research P&L | P&L, sizing, every pairs verdict | **CRITICAL** |
| `bot/research/kalman_filter_engine.py` | 2.3k | Batch Kalman, z-score, forward labels | Signals, IC, features | **CRITICAL** |
| `bot/research/cointegration_engine.py` | 1.7k | Engle-Granger test, half-life, FDR, OOS persistence | Pair selection | **CRITICAL** |
| `bot/research/permutation_test_engine.py`, `permutation_stats.py` | 0.8k | Pairs permutation significance | "edge_appears_real", live win rate | **CRITICAL** |
| `bot/research/walk_forward_engine.py` | 1.5k | Pairs walk-forward | OOS claims | **CRITICAL** |
| `bot/research/oos_validator.py` | 1.4k | Generic fold/purge/embargo, Type A/B/C evaluators | OOS claims for all non-pairs research | **CRITICAL** |
| `bot/research/dca_engine.py` | 1.1k | DCA/Martingale simulator, permutation, OOS | DCA verdicts | **CRITICAL** |
| `bot/research/feature_validator.py` (+ calculator, stability, decay analyzers) | 3.7k | Feature IC, block permutation, FDR | Feature KEEP/DELETE | **CRITICAL** |
| `bot/backtesting/backtester.py`, `bot/engines/simulation.py`, `regime_precomputer.py` | 1.9k | Single-symbol backtest, costs, metrics | Backtest P&L, Sharpe | **CRITICAL** |
| `bot/engines/regime_detector.py`, `strategy_router.py`, `structure_analyser.py`, `bot/strategies/*` | 2.4k | Regime → strategy signals | Signals | HIGH |
| `bot/engines/execution_engine.py`, `order_manager.py`, `position_tracker.py`, adapters, `bot/risk/*` | 3.6k | Single-symbol live execution and risk | Execution, live capital (DRY_RUN) | HIGH |
| `bot/core/bot_runner.py` | 1.0k | Single-symbol live loop | Execution timing | HIGH |
| `bot/research/cross_section_engine.py`, `pca_engine.py`, `cross_sectional_permutation_test.py`, `run_cross_sectional_oos.py` | 2.5k | Cross-sectional / PCA stat-arb research | Research verdicts | HIGH (not deep-audited) |
| `bot/research/contagion_engine.py`, `trade_flow_engine.py`, `derivatives_engine.py`, `regime_conditional_*` | 3.4k | Other research families | Research verdicts | HIGH (not deep-audited) |
| `bot/engines/strategy_scorer.py`, `analytics_engine.py`, `range_analytics.py` | 3.0k | Ranking and metrics | Coin selection, dashboards | MEDIUM |
| `bot/data_fetcher.py`, `fetch_all_symbols.py`, forex/MT5/Yahoo providers | 2.8k | Data acquisition and cache | Data integrity | MEDIUM |
| `bot/research_lab/*` | 4.7k | SaaS research-lab orchestration | Presentation of verdicts | MEDIUM |
| `bot/views/*`, templates, `bot/academy/*` | 5k+ | Dashboard, UI, education | Display only | LOW |
| `bot/management/commands/*` | 4.4k | Health checks | Diagnostics | LOW |
| `bot/tests/*` | ~20k | 190+ tests | — | — |

A full per-module caller/consumer map was **not** produced (see §18). The table above is the prioritization basis.

---

## 3. Critical Quantitative Modules

In order of how much a defect in each can change "has edge" or cost live capital:

1. `entry_exit_engine.py`: P&L and sizing (C-01, H-01, H-02)
2. `kalman_filter_engine.py` and `bot/pairs/kalman_online.py`: spread definition and forward labels (C-02)
3. `permutation_test_engine.py`: significance null (C-03)
4. `bot/pairs/execution.py` and `bot/core/pairs_bot_runner.py`: live exposure (C-04, C-05, H-04)
5. `cointegration_engine.py`: pair selection (H-03, M-09)
6. `dca_engine.py` plus `oos_validator.evaluate_strategy_oos`: DCA verdicts (H-08 to H-10)
7. `feature_validator.py`: feature verdicts (H-12)
8. `backtester.py` and `simulation.py`: single-symbol P&L and metrics (H-11, M-01, M-02)

## 4. Critical Trading Pipelines (traced)

**Pairs (research):** `data/*.csv` → `KalmanFilterEngine` (online-equivalent step; α and β re-estimated each candle) → `kalman_spread` = innovation → rolling z (504, min 168; causal) → `EntryExitEngine._scan_candles` → **P&L = expm1(±Δspread) − 0.3%** (defect C-01) → `win_rate` / Sharpe / entry_ic → `PermutationTestEngine` (defect C-03) → `permutation_verdict.csv` → `bot/pairs/config.py` reads `win_rate` → live Kelly sizing (defect C-06).

**Pairs (live):** ccxt last closed candle (drops the forming candle, correctly) → `kalman_step` → 1h-lagged decision → `_check_entry_conditions` → `KalmanPositionSizer.size_position` (H-01, H-02) → health gate → `PairsExecutionEngine.open_pair_trade` → transfer-in, market buy A, borrow and sell B (C-04, H-06) → `_check_exit_conditions` (4h time stop or z-stop; no partial exit, unlike research, H-05) → close legs, repay → refund collateral.

**Single-symbol:** OHLCV → `RegimeDetector` (causal EWM/rolling; precompute verified causal) → `StrategyRouter` → strategy signal (entry = signal-candle close) → `PositionSizer` (fixed-fractional; math verified) → backtester fills with slippage at SL/TP levels (M-01) → `calculate_pnl` / `calc_fees` (verified) → equity at trade closes only (M-02) → R-multiple Sharpe/Sortino (H-11). The live `bot_runner` feeds the **still-forming** candle into the same pipeline (H-07).

**DCA:** close series → `simulate_dca_cycles` (close-only, one level per candle, trigger measured from the last fill; H-08) → forced `DATA_END_STILL_OPEN` MTM close at each fold end (H-10) → `_compute_trade_metrics` treats DCA `r_multiple` (return on deployed capital) as R (H-11, M-06) → permutation on block-shuffled returns (M-05).

---

## 5 to 11. Defects (mandatory format for CRITICAL and HIGH)

### C-01: Pairs research P&L is the change in the Kalman innovation, not position P&L

> **Remediation status (2026-09-29): FIXED in `entry_exit_engine.py`.**
> * P&L is now the mark-to-market of fixed leg quantities (`qty = leg_usdt / entry price`) at real closes, joined by timestamp from `data/{SYMBOL}_1h.csv`.
> * The partial-exit cost under-charge (cost applied to position × frac²) was fixed alongside.
> * Tests: `bot/tests/test_pairs_pnl_identity.py`, covering the hand-checked identity, the spread-invariance check and the random-walk null calibration.
>
> Re-run on the live config (4h time stop, no target):
>
> | Pair | Trades | Net per trade | Win rate | Sharpe | Profit factor |
> |---|---|---|---|---|---|
> | DODO/FIDA | 647 | −0.29% | 44.8% | −1.90 | 0.62 |
> | MINA/ONG | 679 | −0.43% | 40.2% | −1.46 | 0.47 |
>
> Existing `permutation_verdict.csv` files still hold the old numbers until C-03 re-runs them.
**MODULE:** `bot/research/entry_exit_engine.py` (`_execute_full_exit`, `_execute_partial_exit`) plus the spread definition in `kalman_filter_engine.py` / `bot/pairs/kalman_online.py`
**SEVERITY:** CRITICAL
**CATEGORY:** Mathematical / P&L identity / false evidence of alpha

**CURRENT IMPLEMENTATION:**
`spread_t = log A_t − α_{t|t−1} − β_{t|t−1}·log B_t` (the one-step Kalman innovation). Trade P&L % = `expm1(±(spread_exit − spread_entry)) − 0.003`.

**INTENDED BEHAVIOR:**
The P&L of the position actually held: long `leg_a_usdt/P_A,entry` units of A and short `leg_b_usdt/P_B,entry` units of B (or the reverse), marked from entry fill to exit fill.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:**
`Δspread = Δlog A − β_exit·log B_exit + β_entry·log B_entry − (α_exit − α_entry)`. The terms `(α_exit − α_entry)` and `(β_exit − β_entry)·log B_exit` are *model re-estimation*, not tradeable returns. With Q = 1e-5 and R = 1e-3, the filter's effective local level has a memory of roughly ten candles. It absorbs any deviation within hours, which mechanically pulls the innovation back toward zero after an extreme.

A second, independent problem: even with β frozen, the true P&L as a fraction of `total = leg_a + leg_b` is `Δ(log A − β log B)/(1+β)`. The code omits the `/(1+β)`, which overstates magnitude by (1+β): about 1.8× for DODO/FIDA and 2.6× for MINA/ONG.

**MATHEMATICAL / FINANCIAL EXPLANATION:**
Selecting on an extreme of a (near-)stationary filtered series and measuring its subsequent change is regression to the mean of a *statistic*, not of *prices*. Any adaptive filter converts two random walks into a stationary innovation series, so the "reversion" exists on unrelated assets.

**CONCRETE EXAMPLE:**
Two independent GBMs with 1% hourly volatility, 30,000 candles, driven through the real live `PairSignalEngine`.

**EXPECTED RESULT:** A zero-edge strategy should show about −cost per trade and about 50% gross win rate.

**ACTUAL RESULT:** The research formula gives +1.2% to +1.5% per trade with a 74–81% win rate. True leg P&L gives −0.19% to −0.47% per trade with a 40–46% win rate. Real DODO/FIDA shows +1.13% (formula) vs −0.26% (true); real MINA/ONG shows +0.99% vs −0.35%.

**RESEARCH IMPACT:** Every pairs result is invalid: win rates, Sharpe, profit factor, entry_ic, walk-forward, permutation verdicts, the "Eighth design attempt" validation, and every entry in `model_governance_log.md` that depends on them. Pair-selection work built on these P&L numbers must be re-run.

**RECOMMENDED FIX:** Compute P&L from simulated fills on fixed quantities, marked with real leg prices (both entry and exit, including partial exits), with costs per leg per side plus short-leg borrow interest. Keep the innovation only as a *signal*, never as a P&L proxy.

**TEST REQUIRED TO PROVE THE FIX:**
(a) On independent random walks, mean net P&L per trade is within noise of −cost and gross win rate ≈ 50% (Appendix A probe, turned into a test).
(b) A hand-built 2-candle trade: P&L equals `qa·ΔP_A − qb·ΔP_B` exactly.
(c) A synthetic truly cointegrated pair (OU spread with known half-life) shows positive true P&L before costs.

### C-02: Kalman feature IC is mechanical (label built from the same innovation)
**MODULE:** `kalman_filter_engine.py` `_calculate_forward_returns` (`spread_forward_Nh = spread_{t+N} − spread_t`); consumed by `feature_validator.py`
**SEVERITY:** CRITICAL
**CATEGORY:** Statistical / invalid label / false evidence of alpha

**CURRENT IMPLEMENTATION:** IC = Spearman(pair_signal = −z_t, spread_{t+N} − spread_t).

**INTENDED BEHAVIOR:** The IC of a signal against a *tradeable* forward return of the hedged position.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** As in C-01, the label contains filter re-estimation. For a stationary series, Corr(−x_t, x_{t+N} − x_t) is positive by construction.

**MATHEMATICAL / FINANCIAL EXPLANATION:** For stationary x with variance σ² and lag-N autocorrelation ρ_N, Cov(−x_t, x_{t+N} − x_t) = σ²(1 − ρ_N) > 0.

**CONCRETE EXAMPLE:** Independent random walks, seeds 0 to 2.

**EXPECTED RESULT:** IC ≈ 0.

**ACTUAL RESULT:** **IC = 0.393, 0.427, 0.400.** The headline AVAX/ATOM IC of 0.5245 is only modestly above this pure-noise baseline.

**RESEARCH IMPACT:** The "STRONG KEEP" classification of the Kalman z-score and its lag features is unsupported. Every KEEP decision that rests on a Kalman-spread label is suspect.

**RECOMMENDED FIX:** Label = forward return of the fixed-hedge position formed at t (β, α frozen at t), with position-normalized units.

**TEST REQUIRED TO PROVE THE FIX:** IC on independent random walks within ±2 standard errors of 0 across 20 seeds.

### C-03: Pairs permutation test uses a null that cannot detect C-01/C-02
**MODULE:** `bot/research/permutation_test_engine.py` (`_block_shuffle`, `_compare`)
**SEVERITY:** CRITICAL
**CATEGORY:** Statistical / inappropriate null hypothesis

**CURRENT IMPLEMENTATION:** Shuffles decision columns (z, signal, β, uncertainty, prediction error) in blocks, keeps the real `spread`, re-runs the engine, and compares win_rate and Sharpe. The verdict is `edge_appears_real` when p < 0.05 on both and entry_ic > 0.1. N = 100, so p ≥ 1/101 = 0.0099.

**INTENDED BEHAVIOR:** Reject "no exploitable relationship between the two assets."

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** The null actually tested is "entry timing is independent of the spread series the P&L is computed from." Real z is *computed from* that spread, so real entries always sit at spread extremes and shuffled entries don't. The test is guaranteed to reject for any pair, related or not. It also can't see fixed-quantity P&L at all.

**MATHEMATICAL / FINANCIAL EXPLANATION:** A permutation test is only as valid as the exchangeability assumption under the null. Here, exchangeability is violated by construction under *every* hypothesis about the assets.

**CONCRETE EXAMPLE:** DODO/FIDA and MINA/ONG: win rate 0.87 / 0.83, Sharpe 6.7 / 6.6, p = 0.0099 (the floor) on both metrics.

**EXPECTED RESULT:** A valid test would fail to reject on random-walk inputs.

**ACTUAL RESULT:** It rejects on real pairs at the resolution floor. The C-01 probe shows the same statistics arise on random walks.

**RESEARCH IMPACT:** Every `edge_appears_real=True` verdict is uninformative. That includes the 2 entry-eligible pairs and the 8 now-monitoring pairs whose verdicts were already distrusted for staleness.

**RECOMMENDED FIX:**
* Test with true leg P&L (after C-01).
* Use a null that preserves each asset's own dynamics but destroys the cross-asset relationship: independently block-bootstrap each leg's returns, or circularly shift leg B relative to leg A, and re-run the *entire* pipeline (Kalman filter included).
* Use ≥ 1,000 permutations.
* Report the full null distribution.

**TEST REQUIRED TO PROVE THE FIX:** On 20 independent random-walk pairs the rejection rate at α = 0.05 is ≈ 5%. On a synthetic cointegrated pair with real edge it rejects.

### C-04: Pairs execution leaves naked legs and desynchronizes signal and execution state on common exchange errors
**MODULE:** `bot/pairs/execution.py` (`open_pair_trade`, `close_pair_trade`), `bot/pairs/signal_engine.py`, `bot/core/pairs_bot_runner.py`
**SEVERITY:** CRITICAL (live-capital path)
**CATEGORY:** Execution / exception handling / state contract

**CURRENT IMPLEMENTATION:** `open_pair_trade` catches only `OrderStillOpenException`, `OrderUnconfirmedException` and `RateLimitException`. It doesn't catch `ccxt.InsufficientFunds`, `ccxt.InvalidOrder`, `ccxt.ExchangeError`, a refused `borrow_isolated_margin`, or a negative-quantity order (H-02). Those propagate to `process_pair_candle`'s generic handler, which logs "error". `PairSignalEngine.process_candle` has **already** set `engine.current_trade` before execution is attempted. `close_pair_trade` has the same catch set, and `signal_engine` has already cleared `current_trade` before execution runs.

**INTENDED BEHAVIOR:**
* Any failure after the first leg fills triggers the unwind, collateral refund and pair disable path.
* The signal engine's position state only changes after execution confirms.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:**
* **Open:** leg A buys, then the borrow of B is refused (routine on thin isolated-margin pools such as FIDA or ONG). Leg A is left naked long, collateral sits stranded in two isolated accounts, and the pair is not disabled. The signal engine believes a spread trade is open and later emits EXIT. Execution says "no position tracked", so leg A is never closed by the bot.
* **Close:** leg A's sell fails with InsufficientFunds (see H-06). The signal engine has already dropped the trade, so no exit logic will ever run again for this position. `_open_positions` keeps it forever, and the pair is never disabled.

**MATHEMATICAL / FINANCIAL EXPLANATION:** A market-neutral position becomes a directional, unhedged position of up to about 30% of pair capital. On the short leg, the loss is unbounded until manual intervention.

**CONCRETE EXAMPLE:** DRY_RUN = False, SHORT_SPREAD on MINA/ONG. `open_short(MINA)` succeeds, then `open_long(ONG)` raises `ccxt.InsufficientFunds` because the transfer-in was rounded or partially used by fees.

**EXPECTED RESULT:** The MINA short is bought back, the loan repaid, collateral refunded, and the pair disabled with a CRITICAL log.

**ACTUAL RESULT:** The MINA short stays open with the borrow outstanding and accruing interest. The pair stays tradeable. The signal engine has `current_trade` set, and its eventual EXIT finds nothing to close.

**RESEARCH IMPACT:** None on research. Direct live-capital risk.

**RECOMMENDED FIX:**
* Catch `Exception` (after the specific handlers) around each leg in open and close, and route every failure through unwind, refund and disable.
* Make signal-engine trade state a function of confirmed execution: set `current_trade` only on a successful open and clear it only on a successful close. Otherwise, keep EXIT retrying.
* Persist exit-rule state so restarts can resume.

**TEST REQUIRED TO PROVE THE FIX:** A fault-injection test where the adapter raises each ccxt error class at each step (transfer, buy A, borrow B, sell B, close A, close B). Assert that no base balance or borrow remains, the pair is disabled, and signal state equals execution state.

### C-05: Pairs drawdown guard is never consulted, and its equity measure is wrong
**MODULE:** `bot/pairs/execution.py` (`compute_mark_to_market_equity`), `bot/core/pairs_bot_runner.py`
**SEVERITY:** CRITICAL (live risk control absent)
**CATEGORY:** Risk / dead control

**CURRENT IMPLEMENTATION:** The heartbeat calls `drawdown_guard.update(equity)` and logs `tripped`. No entry path checks it. Equity is the sum over *isolated* accounts only. When flat, collateral has been refunded to spot, so equity ≈ 0, which `update()` ignores. When positions are open, equity ≈ collateral currently deployed.

**INTENDED BEHAVIOR:** Block entries when the pilot's total equity (spot allocation + isolated accounts) falls 15% or more below its peak.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:**
* The guard can't stop anything.
* Even if it were wired in, "peak" is set when two pairs are open and "drawdown" appears when only one is. The metric measures deployment, not loss.

**MATHEMATICAL / FINANCIAL EXPLANATION:** Drawdown needs a constant-scope equity process. The scope here changes with how many positions are open.

**CONCRETE EXAMPLE:** Both pairs open with $75 collateral each gives equity ≈ $150 and a $150 peak. One closes flat, giving equity ≈ $75, a "50% drawdown", and a trip. It is logged only.

**EXPECTED RESULT:** No trip. The actual loss is 0.

**ACTUAL RESULT:** The trip is logged, with no effect in either direction.

**RESEARCH IMPACT:** None. Live risk.

**RECOMMENDED FIX:** Equity = pilot spot USDT allocation + Σ isolated net value. Gate `open_pair_trade` on `not drawdown_guard.tripped`.

**TEST REQUIRED TO PROVE THE FIX:**
* Open and close sequences with zero P&L never trip.
* A −20% equity path blocks the next ENTER.

### C-06: Live position sizing is driven by artifact win rates from a differently configured strategy
**MODULE:** `bot/pairs/config.py` (`_load_validated_win_rate`), `bot/pairs/signal_engine.py`, `KalmanPositionSizer`
**SEVERITY:** CRITICAL
**CATEGORY:** Cross-module contract / sizing

**CURRENT IMPLEMENTATION:** `validated_win_rate` = `permutation_verdict.csv` `win_rate` (DODO/FIDA 0.8716, MINA/ONG 0.8348). Those verdicts show `avg_hold_hours` = 33.7h, while the live config uses `EXIT_TIME_STOP_HOURS = 4` with the target exit disabled.

**INTENDED BEHAVIOR:** The win rate of *this exact live rule set*, measured on true P&L, out of sample.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** Three stacked errors:
* the P&L artifact (C-01),
* a configuration mismatch (a different exit regime), and
* the formula error (H-01).

Code Kelly at p = 0.8716 is 0.853, and quarter-Kelly is 0.213. Combined with the |z|/2 multiplier, nearly every trade is clipped to the 30% maximum.

**MATHEMATICAL / FINANCIAL EXPLANATION:** Kelly on a negative-expectancy bet is f* ≤ 0, meaning no trade. The code bets the maximum.

**CONCRETE EXAMPLE:** The pair capital is $250 and |z| = 2.2, so $250 × 0.213 × 1.1 = $58.6, capped at $75.

**EXPECTED RESULT:** $0, because true expectancy is negative (C-01 probe).

**ACTUAL RESULT:** $58.6.

**RESEARCH IMPACT:** Live sizing is disconnected from any valid evidence.

**RECOMMENDED FIX:**
* Size from OOS true-P&L statistics of the live configuration only.
* Store the rule-set hash alongside the verdict and refuse to load a verdict whose hash doesn't match.
* Fix Kelly (H-01).

**TEST REQUIRED TO PROVE THE FIX:** Config-hash mismatch makes the loader raise. With p = 0.45 and b = 1, the sizer returns 0.

### H-01: Kelly formula ignores the payoff ratio
**MODULE:** `entry_exit_engine.py` `KalmanPositionSizer.__init__`
**SEVERITY:** HIGH
**CATEGORY:** Mathematical

**CURRENT IMPLEMENTATION:** `b = p/q` (win-probability odds), then `f = p − q/b = p − q²/p`.

**INTENDED BEHAVIOR:** Textbook Kelly, `f* = p − q/b` with `b = avg_win/avg_loss`.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** It substitutes win-probability odds for the payoff ratio.

**MATHEMATICAL / FINANCIAL EXPLANATION:** Mean-reversion strategies with time stops typically have b < 1, and the code assumes b = p/q > 1 whenever p > 0.5.

**CONCRETE EXAMPLE:**

| p | b | Code Kelly | True Kelly |
|---|---|---|---|
| 0.6 | 0.8 | 0.333 | 0.100 |
| 0.7 | 0.5 | 0.571 | 0.100 |

(Probe output, Appendix A.)

**EXPECTED RESULT:** 0.100 in both rows above.

**ACTUAL RESULT:** 0.333 and 0.571, an over-bet of 3.3× to 5.7×.

**RESEARCH IMPACT:** Any research sizing or equity curve built on this over-leverages.

**RECOMMENDED FIX:** Estimate b from the train fold. Use f* = max(0, p − q/b).

**TEST REQUIRED TO PROVE THE FIX:** Parametrized cases against hand-computed Kelly, including p ≤ 0.5.

### H-02: Leg split is invalid for β ≤ 0 and explodes near β = −1
**MODULE:** `KalmanPositionSizer.size_position`, `bot/pairs/execution.py`
**SEVERITY:** HIGH
**CATEGORY:** Mathematical / boundary condition

**CURRENT IMPLEMENTATION:** `leg_a = T/(1+β)`, `leg_b = Tβ/(1+β)`, with no sign or magnitude check. The 30% cap applies to T, not to the legs.

**INTENDED BEHAVIOR:** Refuse β ≤ 0 (or handle sign explicitly: same-side legs), and cap each leg.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** With β < 0, leg_b is negative, which produces a negative order quantity (one of the exception paths in C-04). As β → −1 the legs become unbounded.

**MATHEMATICAL / FINANCIAL EXPLANATION:** The hedge ratio's sign flips which side leg B should be on. The formula assumes β > 0.

**CONCRETE EXAMPLE:** The T = $50 trade from the probe:

| β | leg_a | leg_b |
|---|---|---|
| −0.307 | $72.15 | −$22.15 |
| −0.99 | **$5,000** | **−$4,950** |

MINA/ONG's live β has already been observed at −0.307 (per `signal_engine.py`).

**EXPECTED RESULT:** A refused trade, or sign-correct legs capped at the pair's capital.

**ACTUAL RESULT:** Negative or unbounded legs.

**RESEARCH IMPACT:** The live path is protected only by the health gate's sign-*reversal-vs-seed* check. A pair whose *seed* β is negative, or a gate policy change, removes that protection.

**RECOMMENDED FIX:** An explicit guard `β > β_min > 0` in the sizer, plus a per-leg cap.

**TEST REQUIRED TO PROVE THE FIX:** β ∈ {−2, −1, −0.99, −0.3, 0, 1e-6} gives a 0 size or a raise.

### H-03: Cointegration decision uses Dickey-Fuller p-values on estimated residuals
**MODULE:** `bot/research/cointegration_engine.py` `_test_pair`, `_apply_fdr_correction`
**SEVERITY:** HIGH
**CATEGORY:** Statistical

**CURRENT IMPLEMENTATION:** `is_cointegrated = adfuller(OLS residuals).pvalue < threshold`, and BH-FDR is applied to those `adf_pvalue`s. `coint()` (Engle-Granger, MacKinnon cointegration critical values) is computed but used only as a stored column.

**INTENDED BEHAVIOR:** Engle-Granger p-values, because residuals come from an estimated β.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** OLS minimizes residual variance, which makes residuals look more stationary. The DF distribution is the wrong reference, and the resulting p-values are too small.

**MATHEMATICAL / FINANCIAL EXPLANATION:** The EG 5% critical value (2 variables, constant) is ≈ −3.34, vs DF ≈ −2.86.

**CONCRETE EXAMPLE:** MINA/ONG: adf_p = 0.0036 vs coint_p = 0.0161 (4.5× larger). DODO/FIDA: 2.3e-5 vs 1.7e-4.

**EXPECTED RESULT:** The FDR family ranked on `coint_pvalue`.

**ACTUAL RESULT:** Ranked on anti-conservative p-values. Marginal pairs pass that shouldn't.

**RESEARCH IMPACT:** Inflated discovery counts across every scan, including the 100-coin scan.

**RECOMMENDED FIX:** Decide and FDR-correct on `coint_pvalue`. The OOS ADF with frozen β is fine as-is.

**TEST REQUIRED TO PROVE THE FIX:** Size check. On 500 independent random-walk pairs, the rejection rate at 5% ≈ 5% (the current code will exceed it).

### H-04: Pairs live loop drifts and silently skips candles; gaps are treated as one Kalman step
**MODULE:** `bot/core/pairs_bot_runner.py` `run_bot`, `_fetch_last_closed_candle`
**SEVERITY:** HIGH
**CATEGORY:** Execution timing / model consistency

**CURRENT IMPLEMENTATION:** `time.sleep(3600)` *after* each pass, and only the latest closed candle is processed.

**INTENDED BEHAVIOR:** Process every closed candle exactly once, in order, aligned to candle close.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:**
* Loop period = 3600s + work time, so the phase drifts and a candle is eventually skipped (with ~10s work per pass, roughly every 15 days).
* Any machine sleep or outage skips many candles, and the next observation is fed as a single step. Q is per-step, so the filter's dynamics and the 504-window z-score diverge from research.
* Decisions are also delayed by up to about 1 hour after close, which is not the research's lag convention.

**MATHEMATICAL / FINANCIAL EXPLANATION:** A Kalman random-walk state with a missing observation should take N prediction steps (P += N·Q). Here it takes one.

**CONCRETE EXAMPLE:** The laptop sleeps 6h. Five candles are never seen, the spread jump is attributed to one step, and the z-score window's composition changes.

**EXPECTED RESULT:** A backfill of missed candles, or a documented re-bootstrap.

**ACTUAL RESULT:** Silent skip.

**RESEARCH IMPACT:** The live path doesn't replicate the research path.

**RECOMMENDED FIX:** Sleep until next close + ε. On each pass, fetch all closed candles since `last_candle_ts` and process them in order.

**TEST REQUIRED TO PROVE THE FIX:** A simulated clock with a 6h gap processes 6 candles in order, and the state equals a batch replay.

### H-05: Pairs research rule set differs from the live rule set
**MODULE:** `entry_exit_engine._scan_candles` vs `bot/pairs/signal_engine.process_candle`
**SEVERITY:** HIGH
**CATEGORY:** Cross-module contract

**CURRENT IMPLEMENTATION:**
* Research takes a 50% partial exit at |z| < 1.0. Live never calls `_execute_partial_exit`.
* Research's time stop comes from half-life (permutation engine: 1× or 2× HL); live uses a fixed 4h.
* Research costs exclude short-leg borrow interest and use a flat 0.3%.
* Dry-run margin simulation sets `base_interest = 0`.

**INTENDED BEHAVIOR:** One rule set, one cost model, one hash.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** Validated numbers describe a different strategy from the one that trades.

**MATHEMATICAL / FINANCIAL EXPLANATION:** Borrow interest on thin coins over a 4h hold is of the same order as the true gross edge (≈ 0).

**CONCRETE EXAMPLE:** Verdict avg hold 33.7h vs live ≤ 4h (plus stop exits).

**EXPECTED RESULT:** Identical exits on identical data.

**ACTUAL RESULT:** They differ.

**RESEARCH IMPACT:** Even after C-01 is fixed, the verdict must come from the live rule set.

**RECOMMENDED FIX:** A single `PairsRuleSet` dataclass consumed by both paths, plus borrow cost in research and dry-run.

**TEST REQUIRED TO PROVE THE FIX:** A replay test: the research engine and the live `PairSignalEngine` produce identical trade lists on the same candles.

### H-06: Long-leg close sells the gross filled quantity (fee may be charged in the base asset)
**MODULE:** `bot/pairs/execution.py` (`qty_a = fill_a.filled_qty`, `_close_leg`)
**SEVERITY:** HIGH (PLAUSIBLE; requires one live-account verification)
**CATEGORY:** Execution

**CURRENT IMPLEMENTATION:** Closes a long by selling `filled_qty` as reported by the order.

**INTENDED BEHAVIOR:** Sell the *actual* free base balance (from `get_isolated_account`), rounded to lot size.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** On Binance, a market BUY's fee is deducted from the received base asset unless BNB fee payment is enabled. The account then holds `filled × (1 − 0.001)`, so a sell of `filled` fails with InsufficientFunds, which leads straight into C-04.

**MATHEMATICAL / FINANCIAL EXPLANATION:** Balance after fee = q(1 − f).

**CONCRETE EXAMPLE:** Buy 100 DODO, receive 99.9, then try to sell 100.

**EXPECTED RESULT:** Sell 99.9.

**ACTUAL RESULT:** The exchange rejects the order (expected; not observed, since the pilot has never run live).

**RESEARCH IMPACT:** None.

**RECOMMENDED FIX:** Read the free balance before closing, and use `amount_to_precision`.

**TEST REQUIRED TO PROVE THE FIX:** A dry-run simulation that models fee-in-base; plus one live micro-trade verification.

### H-07: Live single-symbol bot computes signals on the still-forming candle
**MODULE:** `bot/core/bot_runner.py` (`build_dataframe`, `process_venue_candle`)
**SEVERITY:** HIGH
**CATEGORY:** Temporal validity / live–backtest mismatch

**CURRENT IMPLEMENTATION:** `fetch_ohlcv(limit=...)` goes straight to the DataFrame, and the last row (the forming candle) is used by the regime detector and strategies. The pairs runner correctly drops it; this runner doesn't.

**INTENDED BEHAVIOR:** Use closed candles only, matching the backtester.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** Signals repaint. The live bot trades on partial information that the backtester never saw.

**MATHEMATICAL / FINANCIAL EXPLANATION:** Close-based indicators (EMA crossovers, RSI, BB) evaluated on a partial close have a different distribution from the closed-candle values they were tested on.

**CONCRETE EXAMPLE:** An EMA cross that appears at minute 10 and is gone by candle close still triggers a live entry.

**EXPECTED RESULT:** No entry.

**ACTUAL RESULT:** Entry.

**RESEARCH IMPACT:** Live results aren't comparable with backtests.

**RECOMMENDED FIX:** `df = df.iloc[:-1]` when the last timestamp + timeframe > now.

**TEST REQUIRED TO PROVE THE FIX:** A unit test with a synthetic forming candle asserts it is excluded.

### H-08: DCA simulator understates crash exposure; the bias is mislabeled "conservative"
**MODULE:** `bot/research/dca_engine.py` (`_maybe_trigger_dca`, `simulate_dca_cycles`)
**SEVERITY:** HIGH
**CATEGORY:** Backtest realism (brief items 2, 3 and 4)

**CURRENT IMPLEMENTATION:**
* Close-only.
* At most one level per candle.
* Each level's trigger is measured from the **last actual fill price**, not the ladder's trigger price.
* The trailing exit is evaluated on closes.

**INTENDED BEHAVIOR:** Model a resting ladder: levels at fixed prices, all crossed levels filled intrabar at their trigger prices (or at the open if gapped through). Report the intrabar-path assumption explicitly.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** In a crash, the simulator deploys far less capital than the real ladder would. After a gap, the whole ladder re-anchors lower, so later levels need a further drop from an already-crashed price. This is **optimistic** for tail risk and exhaustion statistics, which are the main risk measures of a Martingale, not conservative. For exits, close-only trailing can exit either better or worse than a real trail; the direction is path-dependent (UNKNOWN), not conservative.

**MATHEMATICAL / FINANCIAL EXPLANATION:** Exposure is monotone in the number of levels filled, and the simulator fills a subset of the real ladder's levels.

**CONCRETE EXAMPLE:**

| Scenario | Simulated | Real resting ladder |
|---|---|---|
| One candle 100 → 70, then flat | Only L1 at 70.03; deployed **$18** | L1 to L3 fill at 95 / 88.35 / 79.5; deploys **$90** |
| Wick to 80 intrabar, close 99 | **No fill**, $6 deployed | Three fills |

(Both from the Appendix A probe.)

**EXPECTED RESULT:** Real-ladder exposure.

**ACTUAL RESULT:** About 5× understated.

**RESEARCH IMPACT:** The exhaustion rate, worst-cycle loss and max capital are all understated. The docstring's "conservative" claim is false.

**RECOMMENDED FIX:**
* Use OHLC fills against fixed ladder prices, with a gap-at-open rule.
* Run a pessimistic intrabar ordering (low before high for LONG) as the reported case, and an optimistic ordering as a bound.

**TEST REQUIRED TO PROVE THE FIX:** The two probe scenarios above give the real-ladder fills. A multi-level single-candle crash fills all crossed levels in order.

### H-09: DCA volatility scaling uses the full dataset (OOS leakage)
**MODULE:** `dca_engine.compute_volatility_scale_factor`, `get_fx_baseline_config`
**SEVERITY:** HIGH
**CATEGORY:** Leakage (brief item 7)

**CURRENT IMPLEMENTATION:** std(full-history log returns of target) / std(full-history of reference). The scaled config is then evaluated walk-forward with `fit_fn=None`.

**INTENDED BEHAVIOR:** Scale factor fitted per fold on train data only (via `fit_fn`).

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** Test-period volatility shapes thresholds used in that test period.

**MATHEMATICAL / FINANCIAL EXPLANATION:** This is model-selection contamination. Volatility regimes persist, so the full-sample estimate is closer to test-period volatility than a causal estimate would be.

**CONCRETE EXAMPLE:** An FX config's drop and activation thresholds partly set by 2024–2026 volatility, then "tested" OOS in 2024–2026.

**EXPECTED RESULT:** A per-fold causal scale.

**ACTUAL RESULT:** A global scale.

**RESEARCH IMPACT:** FX DCA OOS results are mildly optimistic, with unknown magnitude.

**RECOMMENDED FIX:** Move the scale computation into `fit_fn(train_slice)`.

**TEST REQUIRED TO PROVE THE FIX:** Perturb only test-period returns and assert that fold parameters don't change.

### H-10: DCA metrics mix mark-to-market closes with realized cycles, and measure drawdown only at cycle closes
**MODULE:** `dca_engine.summarize_dca_cycles`, `run_dca_permutation_test`, `oos_validator.evaluate_strategy_oos` / `_replay_fold_bookkeeping`
**SEVERITY:** HIGH
**CATEGORY:** Statistics / risk measurement (brief item 5)

**CURRENT IMPLEMENTATION:**
* `total_net_profit` sums resolved and `DATA_END_STILL_OPEN` cycles.
* The permutation statistic uses that total.
* In walk-forward, every fold's slice ends with a forced MTM close that is scored as a trade.
* Max drawdown is computed from the closed-trade sequence only.

**INTENDED BEHAVIOR:**
* Realized and unrealized kept separate.
* Drawdown computed on a candle-by-candle MTM equity curve.
* Fold-end positions reported as open exposure, not trades.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** A Martingale's risk lives *inside* the cycle as unrealized loss before recovery. A closed-trade drawdown is structurally near zero for DCA, so the metric hides exactly the risk DCA carries.

**MATHEMATICAL / FINANCIAL EXPLANATION:** For a strategy that closes only winners, closed-trade drawdown ≈ 0 regardless of intra-cycle losses.

**CONCRETE EXAMPLE:** A cycle down 60% unrealized for three weeks that then recovers and closes at +1%. Reported drawdown: 0%.

**EXPECTED RESULT:** About 60% of deployed capital.

**ACTUAL RESULT:** 0%.

**RESEARCH IMPACT:** DCA risk reporting is misleading. Walk-forward fold statistics include artificial trades.

**RECOMMENDED FIX:**
* Emit per-candle MTM equity from the simulator.
* Exclude `DATA_END` from realized stats and report it separately.
* Report the maximum intra-cycle adverse excursion.

**TEST REQUIRED TO PROVE THE FIX:** The synthetic V-shaped path above reports its true MTM drawdown.

### H-11: Sharpe and Sortino are non-standard; DCA "r_multiple" enters them as if it were R
**MODULE:** `bot/backtesting/backtester.py` (`calculate_sharpe_ratio`, `calculate_sortino_ratio`), `oos_validator._compute_trade_metrics`, `dca_engine._close_cycle`
**SEVERITY:** HIGH
**CATEGORY:** Statistical / units / terminology (brief item 9)

**CURRENT IMPLEMENTATION:**
* Sharpe = (mean R − rf_per_trade)/std R × √(trades/yr), where rf_per_trade is a *fraction of capital*, subtracted from *R units*.
* Sortino denominator = std of the negative R values only (around their own mean). It returns 999 when there are no losses.
* DCA's `r_multiple` = net P&L / capital deployed (return on deployed capital), which is fed into both formulas.

**INTENDED BEHAVIOR:**
* Sharpe on period returns of equity, with rf in the same units.
* Sortino with downside deviation √mean(min(r − MAR, 0)²) over *all* observations.
* R reserved for P&L / predefined initial risk.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** Unit mixing. The Sortino denominator shrinks when losses are similar in size. Per-trade annualization assumes i.i.d., non-overlapping trades.

**MATHEMATICAL / FINANCIAL EXPLANATION:** See the example below.

**CONCRETE EXAMPLE:** R = [2, 2, 2, −1, −1.1, −0.9] gives code Sortino **5.0** vs the textbook **0.70** (probe).

**EXPECTED RESULT:** 0.70.

**ACTUAL RESULT:** 5.0.

**RESEARCH IMPACT:** Every dashboard and OOS Sortino is inflated. Sharpe is not comparable across strategies. DCA's "avg_r_multiple" is mislabeled.

**RECOMMENDED FIX:**
* Rename DCA `r_multiple` to `return_on_deployed_capital`.
* Compute Sharpe and Sortino from a time-indexed equity curve.
* Remove the 999 sentinel (return NaN or ∞ explicitly).

**TEST REQUIRED TO PROVE THE FIX:** Hand-computed Sharpe and Sortino on fixed vectors. A DCA trade dict contains no `r_multiple` key.

### H-12: Permutation p-value floor combined with family-wide BH makes some research families structurally unable to pass
**MODULE:** `bot/research/feature_validator.py` (`BLOCK_PERMUTATION_N = 100`, `apply_family_wide_correction`)
**SEVERITY:** HIGH
**CATEGORY:** Statistical power / interpretation

**CURRENT IMPLEMENTATION:** p = (k+1)/101 ≥ 0.0099, then BH across a pooled family (symbols × features, often thousands).

**INTENDED BEHAVIOR:** Enough resolution that the correction can be passed by a real effect.

**WHY THE CURRENT IMPLEMENTATION IS PROBLEMATIC:** BH passes rank k only if p_(k) ≤ kα/m. With m = 2,000 and α = 0.05, a floor-level p = 0.0099 needs k ≥ 396 hypotheses *all* at the floor. With m = 2,156 (the contagion family size) it needs about 427.

**MATHEMATICAL / FINANCIAL EXPLANATION:** The test's minimum achievable p exceeds the BH threshold for all realistic k.

**CONCRETE EXAMPLE:** Contagion engine: 2,156 symbol/feature pairs, 0 KEEP.

**EXPECTED RESULT:** A strong true feature can pass.

**ACTUAL RESULT:** Nothing can pass unless hundreds of features are simultaneously extreme.

**RESEARCH IMPACT:** RESEARCH_NEGATIVE verdicts from large families are **uninformative, not evidence of absence**. This affects how the memory notes on contagion and other negative families should be read (it doesn't make them positive).

**RECOMMENDED FIX:** n_perm ≥ ⌈m/α⌉ × a small factor, or a two-stage screen: a cheap analytic test, then permutation on the survivors, correcting within the screened family with proper selective inference.

**TEST REQUIRED TO PROVE THE FIX:** A synthetic family with one planted strong feature among 2,000 nulls is recovered.

---

### MEDIUM and LOW defects (condensed)

| ID | Module | Sev | Finding | Recommended fix |
|---|---|---|---|---|
| M-01 | `backtester._process_exit`, `_open_trade_from_signal` | MED | Gap-through-stop filled at the stop price (OPTIMISTIC); entry at the signal candle's own close (OPTIMISTIC vs next open) | Fill at min(open, SL) for longs; enter at the next open |
| M-02 | `backtester`, `oos_validator._replay_fold_bookkeeping` | MED | Max drawdown sampled only at trade closes, so intra-trade adverse excursion is ignored | Per-candle MTM equity |
| M-03 | `walk_forward_engine._run_single_fold` | MED | `.loc[train_start:train_end]` and `.loc[test_start:…]` with test_start == train_end, so 1 candle sits in both; train trades open at the boundary are force-closed (DATA_END) | Exclusive end; purge by max holding period |
| M-04 | `oos_validator.evaluate_feature_oos` | MED | Spearman p-values assume independence despite overlapping N-horizon labels and pooled cross-sectional rows; the default `fit_fn` (z-score) is a no-op for a rank IC, so "fit" adds no validation | HAC/block-bootstrap IC standard errors; per-timestamp cross-sectional IC then time-series t-stat |
| M-05 | `dca_engine.run_dca_permutation_test` | MED | Shuffling log-return blocks preserves start and end price, so drift (the main driver of a long DCA's profit) is invariant; the null only tests path ordering at > 24h scales; 100 perms; `edge_appears_real` overclaims (brief item 8) | Rename the verdict; add a drift-matched null (e.g. a buy-and-hold benchmark) and ≥ 1,000 perms |
| M-06 | `dca_engine._close_cycle` | MED | `r_multiple` = return on deployed capital, not an R-multiple (brief item 9) | Rename (see H-11) |
| M-07 | `bot/pairs/signal_engine.py` + runner | MED | When execution refuses an ENTER (health gate, disabled pair), the signal engine keeps a phantom `current_trade`, which blocks real entries and later emits an EXIT with nothing to close | Commit signal state only after execution confirms |
| M-08 | Pairs research pipeline | MED | Trading evaluation runs over full history, including the cointegration training window used to select the pair (in-sample selection period scored) | Score only post-selection data |
| M-09 | Cointegration scans (100-coin, 19-symbol rescan, 3 pairs appended 2026-09-28) | MED | BH family is per run; repeated rescans and ad hoc additions create an uncorrected meta-family; no untouched final holdout | Registry of all tests ever run; a discovery/confirmation split; a frozen holdout |
| M-10 | `dca_engine.build_config_with_real_costs` | MED | FX swap/financing absent (documented inline) (brief item 6); results must be labeled economically incomplete wherever displayed | Carry an `economically_incomplete` flag into every FX DCA verdict and UI |
| M-11 | `bot/pairs/health.py` | MED | Beta drift is measured against an OLS seed fitted on the *first* 10,000 candles (2021–22), so "drift" largely measures years of legitimate adaptation, not a recent break; the policy is itself labeled provisional | A recent-window reference (e.g. a trailing 30-day OLS) |
| M-12 | `bot/pairs/margin_adapter._simulate_fill` | MED | Dry-run uses Binance spot fee/slippage, zero borrow interest, and full fills at the ticker "last" price (not bid/ask); the dry-run soak will overstate performance | Model bid/ask and borrow rate (`get_borrow_rate` already exists) |
| L-01 | `dca_engine.compute_theoretical_ladder` | LOW | `recovery_needed_from_original_entry_pct` is the rise from the bottom back to the original entry, and the name is ambiguous; all other ladder formulas were verified correct (weighted average = Σ$ / Σqty) | Rename |
| L-02 | `entry_exit_engine.py` docstrings | LOW | Stale claims ("240h time stop", "IC 0.5245 STRONG KEEP", "68–71% win rate") contradict the current config and this audit | Update after remediation |
| L-03 | `pairs_bot_runner.bootstrap_session` | LOW | Bootstrap takes only ~1,000 candles, and z-score readiness is logged but not required before trading | Require readiness |

### Items verified correct (no defect)
* `oos_validator.build_folds`: the purge removes exactly `horizon` trailing train rows; `assert_temporal_disjoint` is enforced.
* `structure_analyser._find_swing_highs/lows`: only confirmed pivots (loop stops `lookback` before the window end). Causal.
* `regime_precomputer` / `regime_detector`: causal `ewm(adjust=False)` and trailing `rolling`. No centered windows or future shifts found repo-wide (grep for `shift(-`, `center=True`, `bfill` reviewed; the `shift(-N)` hits are forward *labels* only).
* `simulation.apply_slippage`, `calc_fees`, `calculate_pnl`: sign and direction correct.
* `PositionSizer.calculate`: fixed-fractional math correct; floors quantity; caps risk.
* Backtester: SL assumed before TP when both are touched in one candle (conservative).
* DCA: weighted-average entry = total $/total qty (correct), and ladder exhaustion is now measured against `len(config.levels)` (the previously reported defect is fixed and correct).
* Pairs live runner drops the forming candle and requires two independent confirmations to go live.
* `config/settings.py`: `DEBUG = False`, `SECRET_KEY` from env, and no secrets found in tracked files.

---

## 12. Testing Gaps
1. No test compares research P&L against a fixed-quantity replay (C-01). The existing suite validates internal consistency of a wrong identity.
2. No null-calibration tests: nothing checks that a significance test's false-positive rate is ≈ α on synthetic null data (C-03, H-03, H-12).
3. No fault-injection tests for pairs execution across ccxt error classes (C-04).
4. No research-vs-live replay-equivalence test for pairs (H-05).
5. No intrabar or gap scenarios for DCA or the backtester (H-08, M-01).
6. No MTM equity or drawdown tests (H-10, M-02).
7. No boundary tests for β ≤ 0 (H-02) or for the Kelly formula against a textbook reference (H-01).

## 13. Research Methodology Risks
* **Circular labels:** the signal and the label were derived from the same filtered series (C-01, C-02).
* **Accumulating hypotheses:** 8+ pairs design attempts, many exit-rule redesigns, repeated rescans, and sweeps over top_k/timeframe/assets, with no global registry or untouched holdout (M-09). The governance log itself documents a "74 of 76 pairs rejected retroactively" history, which is a strong signal of selection effects.
* **Plausibility checks worked once and should become policy:** the earlier "P&L normalisation bug" was caught by asking whether returns were economically plausible. Sharpe ≈ 6.7 and 87% win rates should have tripped the same check.
* **Negative results from low-resolution tests are not informative** (H-12).

## 14. Security and Production Risks (not deep-audited)
* Settings hardened (verified). View-level auth: 22 `login_required`/mixin usages across 26 URL routes plus the research-lab views; per-route coverage was not verified.
* The pairs pilot relies on in-memory exit state that is not persisted (acknowledged in code). A restart mid-trade disables the pair, which is the safe choice, but the actual margin position is left to manual handling.
* Operator process: the machine-sleep history (memory note) interacts badly with H-04.

## 15. DCA Engine Case Study: status of the 10 brief items

| # | Brief item | Status |
|---|---|---|
| 1 | Full ladder exhaustion semantics | **Fixed and verified correct** (`configured_max_level = len(config.levels)`) |
| 2 | Candle-close execution | **Defect H-08.** The bias is *optimistic* for tail/exposure risk and *UNKNOWN* for exits; the docstring's "conservative" is wrong |
| 3 | Multiple levels in one candle | **Defect H-08.** One level per candle, and the trigger re-anchors to the last fill: $18 vs $90 deployed in the probe |
| 4 | Trailing exit on closes | Close-only. Intrabar trail events are missed; the direction of bias is path-dependent (UNKNOWN) |
| 5 | Data-end open positions | **Defect H-10.** Mixed into `total_net_profit`, the permutation statistic, and walk-forward fold trades |
| 6 | FX costs | Swap/financing absent. Documented in code, but needs an explicit "economically incomplete" verdict label (M-10) |
| 7 | Volatility scaling | **Defect H-09.** Full-sample, which leaks into OOS |
| 8 | Permutation test | **M-05.** Null tests ordering only (drift-invariant); 24h block unjustified; 100 perms (p ≥ 0.0099); `edge_appears_real` overclaims |
| 9 | "R multiple" | **M-06 / H-11.** Return on deployed capital, mislabeled, and consumed downstream as R |
| 10 | Theoretical ladder formulas | Verified: price path, quantity, weighted average, total deployment, target. One ambiguous name (L-01) |

## 16. Severity Matrix

| Severity | IDs | Count |
|---|---|---|
| CRITICAL | C-01, C-02, C-03, C-04, C-05, C-06 | 6 |
| HIGH | H-01 to H-12 | 12 |
| MEDIUM | M-01 to M-12 | 12 |
| LOW | L-01 to L-03 | 3 |

## 17. Recommended Remediation Order (Top 20)
1. **Freeze the pairs pilot in DRY_RUN.** Document that no pairs edge is currently demonstrated (C-01/C-06).
2. Replace research P&L with fixed-quantity leg P&L, plus a random-walk calibration test (C-01).
3. Replace the Kalman forward label with a fixed-hedge forward return, and re-run the feature validation (C-02).
4. Redesign the pairs permutation null (independent-leg or circular-shift, full pipeline, ≥ 1,000 perms) and calibrate it on synthetic nulls (C-03).
5. Re-run the pairs research end to end on corrected P&L. Treat all existing pairs verdicts as void.
6. Fix pairs execution exception handling and signal/execution state commit (C-04, M-07).
7. Wire the drawdown guard into entries with a correct equity scope (C-05).
8. Bind live sizing to a rule-set-hashed OOS verdict, and fix Kelly (C-06, H-01).
9. Add the β-sign and per-leg-cap guard (H-02).
10. Unify the pairs research and live rule set and cost model, including borrow interest (H-05, M-12).
11. Fix the pairs loop scheduling and backfill (H-04).
12. Close long legs using the actual free balance (H-06).
13. Switch the cointegration decision and FDR to Engle-Granger p-values, plus a size-calibration test (H-03).
14. Drop the forming candle in `bot_runner` (H-07).
15. Build a global hypothesis registry, a discovery/confirmation split, and an untouched holdout (M-09, M-08).
16. Fix permutation resolution vs BH in feature validation, and relabel affected negative verdicts as "inconclusive" (H-12).
17. Rebuild DCA on OHLC with a fixed-price ladder, gap and ordering rules (H-08).
18. Add DCA MTM equity, drawdown and realized/unrealized separation; fix fold-end handling (H-10).
19. Make DCA volatility scaling a per-fold fit, and flag FX DCA as economically incomplete (H-09, M-10).
20. Standardize Sharpe/Sortino on equity returns, rename DCA `r_multiple`, and fix backtester gap fills and drawdown (H-11, M-06, M-01, M-02).

## 18. Modules Requiring Re-Audit (coverage statement)

**Deep-audited (code read and, where marked, independently probed):** `bot/pairs/*` (probed), `pairs_bot_runner.py`, `entry_exit_engine.py` (sizer, entry/exit, P&L; probed), `kalman_filter_engine.py` constants and labels (probed), `kalman_online.py`, `permutation_test_engine.py`, `cointegration_engine._test_pair`/FDR/half-life, `dca_engine.py` (probed), `oos_validator.build_folds`/Type A/Type B, `walk_forward_engine` fold construction, `backtester.py` open/exit/run/Sharpe/Sortino (Sortino probed), `simulation.py`, `position_sizer.py`, `drawdown_guard.py`, `execution_costs.py`, `risk.py`, `bot_runner` candle path, `structure_analyser` swing detection, `regime_precomputer`/`regime_detector` causality, `feature_validator` permutation and family correction.

**Not audited in this pass (re-audit required before relying on their verdicts):**
* `cross_section_engine.py`, `pca_engine.py`, `cross_sectional_permutation_test.py`, `run_cross_sectional_oos.py` (the forex top_k=1 "ECONOMICALLY_SUPPORTED" claim rests on these)
* `contagion_engine.py`, `trade_flow_engine.py`, `derivatives_engine.py`, `orderbook_depth_engine.py`, `regime_conditional_*`, `regime_labels.py`
* `feature_calculator.py`, `feature_stability_analyzer.py`, `feature_decay_analyzer.py` (feature-by-feature causal review)
* `strategies/moving_average.py`, `mean_reversion_strategy.py`, `strategy_router.py` (plain-math translation of entry/exit rules)
* `strategy_scorer.py`, `analytics_engine.py`, `range_analytics.py`
* `execution_engine.py`, `order_manager.py`, `position_tracker.py`, Kraken/Binance adapters (live single-symbol path; DRY_RUN)
* `data_fetcher.py` and the forex/MT5 providers (duplicate/out-of-order/missing-candle handling)
* `research_lab/*` (verdict presentation), and view-level auth

---

## 19. Final Requirement: Explicit Answers

**1. Programmatically sound:** `simulation.py`, `position_sizer.py`, `drawdown_guard.py` (the class itself), `oos_validator.build_folds`, `structure_analyser` swing detection, `regime_detector`/`regime_precomputer`, `kalman_online.kalman_step`/`RollingZScore` (a faithful replica of the batch filter), `cointegration_engine` mechanics, `dca_engine` ladder arithmetic, `health.py`, `margin_adapter.py` (as far as can be verified without live calls).

**2. Quantitatively sound (for their stated purpose):** `simulation.py` cost primitives, `PositionSizer`, `build_folds` purge logic, and the DCA theoretical ladder formulas. **No strategy-level module is currently quantitatively sound.**

**3. Passing tests but unresolved quantitative risk:**
* `entry_exit_engine.py`, `kalman_filter_engine.py`, `permutation_test_engine.py`, `walk_forward_engine.py`, `cointegration_engine.py`
* `dca_engine.py`, `feature_validator.py`, `backtester.py` (metrics)
* `bot/pairs/*`, `oos_validator.evaluate_feature_oos`/`_compute_trade_metrics`

**4. Can contaminate backtest results:** `entry_exit_engine` (C-01), `kalman_filter_engine` labels (C-02), `backtester` fills and metrics (M-01, M-02, H-11), `dca_engine` (H-08, H-10), `oos_validator._compute_trade_metrics` (H-11), `walk_forward_engine` boundary (M-03).

**5. Can create look-ahead bias:** `dca_engine.compute_volatility_scale_factor` (H-09), `walk_forward_engine` 1-candle overlap (M-03), live `bot_runner` forming candle (H-07; a live/backtest inconsistency rather than a backtest leak), and the pairs pipeline scoring its own selection window (M-08). No centered windows or future shifts were found in feature code; causal review of every individual feature is still pending (§18).

**6. Can create false evidence of alpha:** `entry_exit_engine` P&L (C-01), Kalman IC labels (C-02), `permutation_test_engine` (C-03), `cointegration_engine` DF p-values (H-03), backtester Sortino/Sharpe (H-11), DCA permutation null (M-05), multiple-testing meta-family (M-09).

**7. Affect live capital risk:** `bot/pairs/execution.py`, `margin_adapter.py`, `signal_engine.py`, `config.py`, `health.py`, `pairs_bot_runner.py`, `KalmanPositionSizer`, `DrawdownGuard` wiring; and on the single-symbol side `bot_runner.py`, `execution_engine.py`, `order_manager.py`, `position_sizer.py`.

**8. Require independent mathematical verification:** every pairs P&L/label/sizing path; the Kelly sizer; Sharpe/Sortino; the DCA simulator with OHLC; every permutation test (null calibration); the cointegration test size; cross-sectional and PCA portfolio P&L (not yet audited).

**9. Require new synthetic tests:** random-walk null calibration (pairs P&L, IC, permutation, cointegration size); known-OU cointegrated pair (power); fixed-quantity P&L identity; DCA crash, wick and gap scenarios; MTM drawdown on a V-path; β-boundary sizing; ccxt fault injection; forming-candle exclusion; missed-candle backfill; planted-feature recovery under BH.

**10. Top 20 remediation priorities:** §17.

**11. Must be fixed before further strategy development:** C-01, C-02, C-03 (the measurement and inference stack), H-03 (cointegration size), H-12 (resolution vs BH), M-09 (hypothesis registry and holdout), H-11 (metric definitions). Without these, new strategies will be evaluated by the same instruments that just produced a false positive.

**12. Must be fixed before paper trading:** all of item 11, plus C-06, H-01, H-02, H-04, H-05, M-07, M-12, H-07, and a pairs rule set that shows **positive true-P&L OOS on an untouched holdout**. Paper trading an edge that isn't demonstrated only measures costs.

**13. Must be fixed before any live capital:** everything above, plus C-04, C-05, H-06, persisted exit state, one live micro-trade verification of fee-in-base and borrow/repay semantics, and a paper-trading period whose realized P&L is consistent with the OOS estimate.

---

## Appendix A: Reproduction probes (read-only; production code unmodified)

**A.1 P&L identity (C-01).** Drives the real `bot.pairs.signal_engine.PairSignalEngine`. It bootstraps 1,000 candles, processes the rest, and on each ENTER/EXIT records (a) the research formula `expm1(±Δspread) − 0.003` and (b) the true `±(qa·ΔP_A − qb·ΔP_B)/total − 0.003` using the engine's own `leg_a_usdt`/`leg_b_usdt` and fill-candle prices. Inputs:
* 5 seeds of two independent GBMs (σ = 1%/h, 30,000 candles)
* `data/DODO_USDT_1h.csv` + `data/FIDA_USDT_1h.csv`, and MINA/ONG, seeded with the live config's hedge_ratio/intercept

The script is in this session's scratchpad as `audit_pnl_identity.py`; the core loop is:

```python
eng = PairSignalEngine(cfg); eng.bootstrap_from_history(list(zip(pa[:1000], pb[:1000])))
for i in range(1000, len(pa)):
    d = eng.process_candle(ts[i], pa[i], pb[i])
    if d.action == "ENTER": o = dict(pa=pa[i], pb=pb[i], la=d.trade.leg_a_usdt, lb=d.trade.leg_b_usdt,
                                    tot=d.trade.position_usdt, dirn=d.trade.direction, s0=d.trade.entry_spread)
    elif d.action == "EXIT" and o:
        sgn = 1 if o["dirn"] == "LONG_SPREAD" else -1
        model = np.expm1(sgn*(d.native.spread - o["s0"])) - 0.003
        true  = sgn*((o["la"]/o["pa"])*(pa[i]-o["pa"]) - (o["lb"]/o["pb"])*(pb[i]-o["pb"]))/o["tot"] - 0.003
```

**A.2 IC on noise (C-02).** `kalman_step` + `RollingZScore` on independent random walks, then Spearman(−z, spread_{t+4} − spread_t). Result: 0.393 / 0.427 / 0.400.

**A.3 DCA / Kelly / leg split / Sortino (H-08, H-01, H-02, H-11).**
* `simulate_dca_cycles(BASELINE_CONFIG)` on [100, 70, 70, …] fills levels [0, 1] only. On a −20% wick with close −1%, it fills [0] only.
* Kelly: code vs textbook at (p, b) = (0.6, 0.8), (0.6, 1.0), (0.7, 0.5).
* Leg split at β ∈ {0.8, −0.307, −0.99}.
* Sortino on [2, 2, 2, −1, −1.1, −0.9].
