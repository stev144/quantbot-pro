# Steph Quant Technologies — What Is Still Not Fixed (status audit, 2026-10-02)

**Mode:** read-only. No code changed.

**Method:**
1. Every finding of the 2026-09-29 forensic audit (`QUANT_AUDIT_REPORT_2026-09-29.md`, 33 findings) was re-checked against the **current code** (commit `565e55d`, branch `stage2d-revb`).
2. `bot/research/dca_engine.py` was audited in full for the first time in the Stage 2D programme.
3. The open Stage 2D items were collected from the protocol, decision-lock and audit documents.

**Scope honesty:**
- Covered: every module named in the 09-29 audit, plus the DCA engine.
- **Not audited** in this pass: dashboard views, research lab, academy, the Kraken/multi-venue execution layer, the cross-sectional, PCA and contagion engines, `data_fetcher`, and the main bot's strategies and regime detector (beyond findings H-07, M-01, M-02 and H-11).

---

## 1. Headline

| | Count |
|---|---|
| Prior findings now **fixed** | 12 |
| Prior findings **partially fixed** | 4 |
| Prior findings **still open** | 17 |
| **New** findings in this pass | 5 |

**Money at risk today:**
- The pairs pilot is DRY_RUN, and new live entries are blocked (no canonical seed or eligibility exists).
- The DCA engine is research-only: nothing live or on the dashboards imports it.
- The **main single-symbol bot** (`bot/core/bot_runner.py`, DRY_RUN by default) still carries H-07 and the backtester issues.

---

## 2. Status of every 2026-09-29 finding

| ID | Area | Status | Evidence / what remains |
|---|---|---|---|
| C-01 | Pairs research P&L | **FIXED** | Fixed-quantity P&L via `bot/pairs/pnl.py` (Stage 2D baseline) |
| C-02 | Mechanical Kalman IC labels | **FIXED** | `spread_forward_*` labels retired (`assert_label_admissible`) |
| C-03 | Pairs permutation null | PARTIAL | Timing null implemented, but the engine still runs the **LEGACY** rule set with B = 100. The canonical permutation is Stage 2D Step 17 |
| C-04 | Naked legs / desync | PARTIAL | Phantom trade on refusal **fixed** (`abort_entry`). **Still open:** `open_pair_trade` catches only 3 exception types (`execution.py:169`). Any other exchange error on leg B after leg A filled (e.g. insufficient funds, a network error) leaves **leg A naked**: no unwind, no `LEG_MISMATCH`, and the engine keeps a phantom trade |
| C-05 | Drawdown guard | **OPEN** | `drawdown_guard.update()` runs only in the heartbeat log (`pairs_bot_runner.py:563`). `tripped` is **never consulted** before an entry |
| C-06 | Artifact win rates drive sizing | **FIXED** | Kelly removed; eligibility is canonical-only |
| H-01 | Kelly ignores the payoff ratio | **RESOLVED** | Kelly removed |
| H-02 | Leg split invalid for β ≤ 0 | **FIXED** | SKIP outside the β > 0 domain |
| H-03 | Dickey-Fuller p-values on residuals | **FIXED** | Engle–Granger with MacKinnon p-values (research). A canonical *selection zone* is still pending |
| H-04 | Live loop skips candles; gaps compressed | MOSTLY FIXED | Grid stepping, D8 gap policy, reconciliation block. **Backfill/reconciliation of missed candles: OPEN** (deferred) |
| H-05 | Research ≠ live rule set | PARTIAL | Live and canonical research share one lifecycle. Walk-forward, permutation and the research lab still run LEGACY rules (now labelled LEGACY) |
| H-06 | Long-leg close quantity | **FIXED** | `sellable_quantity` |
| **H-07** | Main bot signals on the forming candle | **OPEN** | `bot_runner.py:565` fetches OHLCV including the still-forming candle, and `build_dataframe` doesn't drop it. Signals repaint |
| **H-08** | DCA understates crash exposure | **OPEN** | See §3 D-a |
| **H-09** | DCA volatility scaling uses the full dataset | **OPEN** | `compute_volatility_scale_factor` uses the full-history std of both series, which leaks into OOS |
| **H-10** | DCA metrics mix MTM with realised; drawdown at closes only | **OPEN** | See §3 D-c |
| **H-11** | Non-standard Sharpe/Sortino | **OPEN** | `backtester.calculate_sharpe_ratio` works on R-multiples and subtracts a risk-free *return* per trade from R (a unit mismatch). DCA's "r_multiple" is a return on deployed capital fed into the same formula |
| **H-12** | Permutation floor vs BH family | **OPEN** | `feature_validator.BLOCK_PERMUTATION_N = 100`, so p ≥ 0.0099 cannot pass BH in large families. Negative verdicts there remain uninformative |
| **M-01** | Backtester fills | **OPEN** | Entry at the signal candle's price; a gap through the stop fills at the stop (optimistic) |
| **M-02** | Drawdown sampled at closes | **OPEN** | `backtester.py`, `oos_validator.py` unchanged since the audit |
| **M-03** | Walk-forward boundary overlap | **OPEN** | `walk_forward_engine.py:720` `test_start = train_end` puts 1 candle in both train and test. No purge |
| **M-04** | OOS validator p-values assume IID | **OPEN** | `oos_validator.py` unchanged |
| **M-05** | DCA permutation null | **OPEN** | See §3 D-d |
| **M-06** | DCA "r_multiple" mislabelled | **OPEN** | `_close_cycle` sets `r_multiple = net / deployed` |
| M-07 | Phantom trade on refused ENTER | **FIXED** | `abort_entry()` (2026-10-02) |
| M-08 | In-sample selection period scored | **FIXED (canonical)** | Causal seed; no fitted span inside canonical output. The legacy path still relies on the `is_training_window` exclusion |
| **M-09** | No registry / untouched holdout | **OPEN** | Zones, forward holdout start F and the global hypothesis registry are BLOCKED (owner decisions) |
| **M-10** | FX swap absent in DCA | **OPEN** | Documented inline. No `economically_incomplete` flag is carried into results |
| **M-11** | Health drift vs the 10k-candle seed | **OPEN** | `bot/pairs/health.py` unchanged. It still measures drift against `cointegration_pairs.csv`'s legacy seed |
| **M-12** | Dry-run fills optimistic | **OPEN** | `_simulate_fill`: ticker "last" price × flat slippage, zero borrow interest, full fills |
| **L-01** | DCA ladder field naming | **OPEN** | `recovery_needed_from_original_entry_pct` is ambiguous |
| **L-02** | Stale docstrings in `entry_exit_engine.py` | **OPEN** | Cosmetic |
| L-03 | Bootstrap ~1,000 candles | **FIXED** | Canonical replay from the causal seed |

---

## 3. DCA engine audit (first full audit; research-only, not wired live)

**Verified correct:**
- The cost basis is total $ / total units. A known prior bug in `compute_theoretical_ladder` was already fixed.
- LONG/SHORT P&L mirror, fees on both sides, and slippage direction.
- The trailing-exit state machine.
- Unresolved cycles are never dropped.
- Exhaustion in `summarize_dca_cycles` counts against the **configured** levels.
- The z-score and ATR research features are causal.

| ID | Severity | Finding | Why it matters |
|---|---|---|---|
| **D-a (H-08)** | HIGH | `_maybe_trigger_dca` fills **at most one level per candle**, evaluated on the close and filled **at that close**. In a candle that falls through several levels, a real trigger-order ladder fills each level at its trigger, deploying more capital at higher prices. The simulator fills one level at the crashed price. | It **understates capital deployed and the cost basis**, so profitability is overstated. The docstring's claim that this is a "conservative bias" is wrong. The direction is optimistic. |
| **D-b (new)** | HIGH | No execution delay: exits and DCA levels are **decided and filled on the same close**, and a new cycle opens **on the exit-fill candle** itself. | Inconsistent with the Stage 2D clock (decide at t, fill at t + 1; no decision on a fill close). It makes every result optimistic by the same-close assumption. |
| **D-c (H-10)** | HIGH | `total_net_profit` adds DATA_END mark-to-market "closes" to realised cycles. There is **no intra-cycle drawdown / adverse excursion** metric; only `worst_cycle_profit` at close. | The defining tail risk of a martingale (unrealised loss while trapped, measured in capital-at-risk time) is **not measured**. |
| **D-d (M-05)** | HIGH | The permutation shuffles 24 h blocks of log-returns. The sum of returns is invariant, so **start and end price are identical in every permutation**: drift, the main driver of a long DCA's profit, cannot be tested. B = 100 (p floor 0.0099). | `edge_appears_real` from this test does not test what it claims. |
| **D-e (H-09)** | MED | The FX volatility scale factor uses full-sample std. | Look-ahead in any OOS use of the FX config. |
| **D-f (new)** | MED | SHORT cycles assume **free shorting**: no borrow/financing (crypto spot can't short without margin) and no FX swap (M-10). | Under the Stage 2D D11 principle, an unknown borrow cost must be explicit (COST_UNAVAILABLE), never zero. |
| **D-g (new)** | MED | `summarize_feature_experiment`: `eventual_ladder_full_exhaustion_rate_pct` uses the **deepest level observed within each bucket**, not the configured 6 levels (`dca_engine.py:840`). This is the same bug class already fixed in `summarize_dca_cycles`. | Exhaustion rates are not comparable across buckets. A bucket that never reached level 6 reports "exhaustion" anyway. |
| **D-h (new)** | LOW | No grid/gap handling: rows are iterated as consecutive, and `candles_held` (and `max_cycle_candles`) counts rows, not wall-clock hours. | Gaps are compressed, the same class as the fixed pairs D8 issue. |
| M-06 / H-11 | MED | `r_multiple` is a return on deployed capital and enters a Sharpe built for R-multiples. | Wrong risk-adjusted metrics in OOS reports. |
| — | GOVERNANCE | DCA outputs carry no provenance (`rule_set`, methodology, data version). It was deliberately excluded from Stage 2D. | DCA results can't be distinguished by methodology. |

---

## 4. Other new findings (this pass)

| ID | Severity | Finding |
|---|---|---|
| N-1 | MED | **Exit-price minNotional is not enforced.** The protocol §6 requires rejecting at entry *and* exit prices. `accounting.exit_notional_ok` exists but has **no caller**. A position can shrink below minNotional and become un-closable by a market order. |
| N-2 | OPS | **Migration `0012` is not applied** to the operational database. It must run before the pairs bot is restarted. |

---

## 5. Stage 2D programme — still open by design

| Item | Status |
|---|---|
| Step 12 (borrow-unavailable explicit; dry-run interest currently inferred as 0) | ready to implement |
| Steps 13–20 (purge/embargo with real boundaries, OOS/WF, statistical corrections, multiple-testing registry, canonical permutation, regression) | not started |
| Research zones S / V / WF / F / L_H | **BLOCKED** (owner) |
| Canonical selection/eligibility producer | not built, so **every pair is blocked for new live entries** |
| Missed-candle backfill/reconciliation | deferred |
| FX market calendar + FX gap limit | **BLOCKED** (no authoritative source) |
| Legacy engines (walk-forward, permutation, research lab) migrated to the canonical rule set | Steps 13–17 |
| L = 504 sensitivity run | to be done on development data when results exist |

---

## 6. Suggested priority (my recommendation)

1. **Before any live money (pairs):** C-04 naked-leg handling (catch every exchange exception after leg A fills, then unwind and flag); C-05 consult the drawdown guard before entries; N-1 exit-notional check; M-12 realistic dry-run fills; N-2 migration.
2. **Main bot:** H-07 (drop the forming candle). Then M-01, M-02, H-11 in the backtester that scores it.
3. **DCA, before trusting any DCA verdict:** D-a, D-b, D-c, D-d (a drift-preserving null is not enough: test against a data-generating null and report drift separately), D-f, D-g, then D-e, D-h.
4. **Research statistics:** H-12 (resolution vs BH), M-03, M-04, then Stage 2D Steps 13–20 once the zones are decided.
5. Cosmetic: L-01, L-02.
