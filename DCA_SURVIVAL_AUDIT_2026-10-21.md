# DCA Capital-Survival Upgrade — Audit, Implementation & Results (2026-10-21)

Source brief: `prompt.txt` ("Professional DCA Engine Capital-Survival, Execution-Cost & Historical Stress-Test Upgrade").
Status: **IMPLEMENTED (forex QC-1 scope).** §1–§6 are the original Phase A audit; §7 onward records the owner
decisions, what was built, the results and the limitations. Nothing is committed yet.

---

## 1. There are two DCA engines, not one

| | Crypto engine | Forex QC-1 engine (what `/execution/dca-engine/` shows) |
|---|---|---|
| Simulator | `bot/research/dca_engine.py::simulate_dca_path` (+ `simulate_dca_path_fx` / `_simulate_fx_next_open`, an FX variant that is not used by the page) | `bot/research/fx_fold_runner.py::_simulate_data_derived_next_open` |
| Driver | research scripts, `oos_validator`, deep-health L1 | `bot/views/dca_engine.py` → `fx_multipair_runner.run_multipair_fold` |
| Copies of the loop | — | `bot/paper_trading/ranging_paper_engine.py` (imports the fx_fold_runner loop), `bot/research/fx_live_paper.py`, `bot/research/dca_wf_validator.py` |
| Instrument | XRP/USDT 1h, spot, long | GBP/JPY 1h (+4 QC pairs), long, 1:30 leverage claimed in display only |
| Ladder | 7 fills: $6·12·24·48·96·192·384 = **$762**; drops 5/7/10/15/15/10 % from previous fill | 5 fills: 1·2·4·8·16 × entry (LCEX-1); spacing = calibration MAE quantiles × ATR ratio |
| Exit | +2.5 % from WAP activates, 0.5 % trail; **no stop, no time limit** | +0.5 % activates, 0.2 % trail; **72 h time exit** |
| Costs | taker fee 0.001 + slippage 0.0005 per fill (`bot/config/execution_costs.py`) | spread / commission / swap fields exist but are **all `None` → $0** (`forex_instrument_spec.py` PLACEHOLDER specs) |

**The brief's "reported baseline" (§3 of prompt.txt) is the crypto engine.** The dashboard the brief says to upgrade is the forex one. §3 of the brief also appears to have lost its first lines in copy/paste (heading + first bullets missing).

## 2. Baseline reproduction (fixed data, current code, 2026-10-21)

**Crypto — XRP/USDT 1h, 2022-01-01 → 2026-09-24 22:00, `BASELINE_CONFIG`, NEXT_OPEN fills**
- 353 cycles (352 closed, 1 open at data end, marked −$0.36); closed-cycle win rate 97.16 %
- Net P&L **+$379.77** after $16.44 fees (slippage is embedded in fill prices, not itemised)
- Max capital deployed $762 (full ladder exhausted 2×); worst floating loss −$111.20; max MTM drawdown −$114.78
- Longest cycle 2,487 h (~104 days); longest underwater spell 3,553 h, unrecovered at data end
- Largest single cycle = 28.4 % of all profit

**Forex — GBP/JPY, page as served today**
- 512 closed cycles, 82.4 % win, **+$9,524.53** = $19.05 simulated at $6/cycle × 500 display scale
- Gross = net (zero costs). With a 2.5-pip spread proxy: +$4,006.58 (spread-only, estimated)
- Page "max drawdown" −$3,486.58 is **closed-trade** only; MTM peak-to-trough is −$5,607.56 (2024-08-05); worst floating loss −$4,619.05 (2022-09-26); lowest cumulative MTM equity −$4,203.13
- 6 cycles open at fold ends are **dropped** from the totals (−$124.38 combined)

## 3. Lifecycle & accounting flow (as implemented)

1. Flat at bar close → schedule OPEN → fill at next bar's open (+slippage / +spread).
2. In cycle: mark to market at close; check profit activation → trail; (forex) time exit; else check next ladder trigger vs previous fill → schedule DCA → fill next open.
3. EXIT fills at next open; P&L = (exit − WAP) × qty − costs. Next cycle may open on the following bar.
4. End of data: crypto → censored liquidation mark, labelled, kept in MTM totals. Forex page → `DATA_END_STILL_OPEN` **excluded**.

Both engines produce a per-bar MTM frame (realised / unrealised / equity **P&L from 0**), and the crypto one also records `capital_deployed` and `ladder_depth`.

## 4. Deficiencies found (graded)

| # | Severity | Finding | Where |
|---|---|---|---|
| D1 | **Critical** | No account. Equity is cumulative P&L from 0; there is no starting capital, cash, free cash or reserved capital, and no rejection when an order can't be funded. Capital is effectively unlimited — every ladder level always fills. | both simulators |
| D2 | **Critical** | Forex costs are zero: spread, commission and swap are all `None` on every QC spec, so `gross == net`. | `forex_instrument_spec.py` |
| D3 | **Critical** | Leverage/margin exist only as a display multiplier (`_DISPLAY_SCALE = 500`). No margin used, margin level, ESMA 50 % close-out or liquidation is modelled, so the page cannot show a blow-up even when the reconstructed account would be negative (2022-09-26). | `views/dca_engine.py`, `fx_fold_runner.py` |
| D4 | High | Page drawdown is closed-trade only; it hides open-cycle losses (−$3.5k shown vs −$5.6k MTM). | `views/dca_engine.py` |
| D5 | High | Open cycles at fold ends are discarded; cycle state and P&L restart at every fold boundary (silent reset) and nothing is simulated across the 48-bar embargo gaps. | `views/dca_engine.py`, `fx_fold_runner.py` |
| D6 | High | The page's fold boundaries are 70/30 % of the *current* CSV length; the hourly MT5 refresh moves them, so the page totals change with every refresh and fold 6 now reaches into 2026-10-19+ (QC-2 window). The header date "2026-10-02" is stale. | `views/dca_engine.py`, `fx_fold_splitter.py` |
| D7 | Medium | No itemised cost ledger: crypto slippage is folded into fill prices, and no per-fill fee/slippage/financing records exist, so no cost-attribution ratios can be computed. | `dca_engine.py` |
| D8 | Medium | Three copies of essentially the same FX cycle loop (`fx_fold_runner`, `dca_engine._simulate_fx_next_open`, ranging paper engine) — conflicting sources of truth. | as listed |
| D9 | Medium | Outcome states are only exit reasons (`PROFIT_TARGET_TRAILING`, `TIME_EXIT`, `DATA_END_STILL_OPEN`); no LADDER_EXHAUSTED / INSUFFICIENT_FREE_CASH / MARGIN_CALL / LIQUIDATED. | both |
| D10 | Low | Hourly bars only; intrabar ordering is unknowable (NEXT_OPEN convention sidesteps it; gaps fill at the open, which is correct). | data |
| D11 | Low | Pre-existing test failure: `test_dca_phase2...test_fx_entry_points_report_fx_calendar_unavailable` (FX is now accepted where the test expects refusal — likely from the 10-18 market-agnostic refactor). | tests |

Not deficiencies (verified): crypto fees are per fill on both sides; NEXT_OPEN prevents look-ahead; crypto open cycles at data end are kept and labelled; one cycle per symbol at a time (no overlapping cycles to share capital, so portfolio capital sharing only arises across symbols).

## 5. Proposed file-level plan (staged; each stage ends with focused tests + baseline diff)

**Principle:** one new account/ledger layer that *consumes fills* from the existing simulators. The ladder, entry, exit and cost conventions are not rewritten, so the baseline output stays reproducible bit-for-bit when capital is unlimited and costs are unchanged.

| Stage | Files | Change | Risk |
|---|---|---|---|
| S1 Ledger | **new** `bot/research/dca_account.py` | `AccountModel` (SPOT_CASH / LEVERAGED_FX_MARGIN), `Ledger` (timestamped FILL / FEE / SPREAD / SLIPPAGE / SWAP / FUNDING / REALISED / LIQUIDATION rows), per-bar state (cash, reserved, free, margin used, margin level, equity, peak, DD), reconciliation identity asserted every bar | new code only |
| S2 Hooks | `dca_engine.py`, `fx_fold_runner.py` | optional `account=None` parameter; when given, each fill asks the account first → may return `INSUFFICIENT_FREE_CASH` / `ORDER_REJECTED`; costs itemised from the same formulas already used (slippage = price diff vs mid) | must be a no-op when `account=None` — regression test asserts identical trades |
| S3 Outcomes | same + `dca_account.py` | classification states from the brief §8 + failure record per event; ESMA 50 % margin close-out for FX (configurable, labelled approximation); spot never "liquidated" | — |
| S4 Continuous replay | **new** `bot/research/dca_survival_replay.py` | single chronological 2022→data-end run per symbol, fixed starting capital, no fold resets, open cycles kept; frozen data cutoff in the run config (fixes D5/D6) | must respect QC-2 seal for EUR/CAD and crypto zone F |
| S5 Costs | `forex_instrument_spec.py`, `execution_costs.py` | fill FX spread/commission/swap from a cited source (see decision Q2), tagged ESTIMATED; cost-attribution metrics A–F with N/A on zero denominators | no invented numbers |
| S6 Events | **new** `bot/research/data/market_events.json` | sourced event list (2022 hikes, UK mini-budget 2022-09, Terra/FTX, SVB 2023-03, BoJ 2024-07-31/08-05, …) + measured per-window stats | descriptive only, never a signal |
| S7 Stress | **new** `bot/research/dca_stress.py` | capital levels, cost/slippage multipliers, gap shocks, delayed candles; historical vs hypothetical classes separated | — |
| S8 Dashboard | `views/dca_engine.py`, `templates/dca_engine.html` | account-risk cards, cost breakdown, coordinated balance/equity/free-cash/margin/DD charts with event and liquidation markers; baseline-vs-upgraded panel; fix stale header/badge | page cache key bump |
| S9 Tests/report | **new** `bot/tests/test_dca_account.py`, `test_dca_survival_replay.py` | brief §13 list; full suite; comparison report | — |

Commands (to be confirmed at each stage): `venv/Scripts/python.exe manage.py test bot.tests.test_dca_account` … `manage.py test bot`; dashboard `manage.py runserver` → `/execution/dca-engine/`.

## 6. Owner decisions needed before S1

- **Q1 Scope:** crypto XRP engine (matches the brief's baseline), forex QC-1 engine (matches the page), or both through the shared ledger.
- **Q2 FX costs:** source for spread/commission/swap — the connected MT5 broker's current `symbol_info` (real but *current*, not historical → labelled ESTIMATED), or leave UNAVAILABLE and show gross only.
- **Q3 Account rules:** starting-capital levels and the FX close-out rule (default proposal: ESMA retail 50 % margin close-out, 1:30 for majors / 1:20 for minors per ESMA — to be checked against each pair).

---

## 7. Owner decisions (2026-10-21)

| | Decision |
|---|---|
| Scope | **Forex QC-1 only** (the engine behind `/execution/dca-engine/`). Crypto XRP engine untouched. |
| FX costs | **MT5 broker** — per-bar spread history (`MqlRates.spread`) + current swap snapshot. Commission unavailable → $0, stress-tested. |
| Account rules | **ESMA retail** — 1:30 majors / 1:20 others, 50 % margin close-out, negative-balance protection. |
| Capital | **$3,100 / $5,000 / $10,000** (full-ladder margin, $5k, $10k). |
| Data cutoff | **2026-10-16 08:00 UTC** = `FX_RESEARCH_DATA_CUTOFF` = min(v7 holdout A6-3, QC-2 holdout). Stricter than the 10-19 cutoff first used in the morning walk-forward run. |

Broker facts read from MT5 (read-only): IC Funded Evaluations demo, USD account, account leverage 1:100, margin call 100 %, stop-out 50 %; GBP/JPY swap long +9.69 / short −22.47 points/lot/day, triple on Wednesday; contract 100,000, min/step 0.01 lot.

## 8. What was built

One account layer on top of the existing strategy code — no second strategy implementation:

- `fx_fold_runner.decide_in_cycle()` — the legacy loop's decision block moved verbatim into a function; the legacy loop and the new replay both call it. Verified identical: legacy total $8,542.15 before and after on frozen data; a test asserts trade-by-trade equality (entries, exits, fills, reasons, P&L to 1e-9) with unlimited capital and zero costs.
- `bot/research/fx_survival_replay.py` — chronological multi-pair replay with ONE shared USD account: balance, MTM equity, margin used (current base→USD rate), free margin, margin level, timestamped cash ledger (REALISED_TRADING_PNL, COMMISSION, SWAP, NEGATIVE_BALANCE_PROTECTION); spread/slippage itemised separately (inside fill prices, never double-counted); order rejection (INSUFFICIENT_FREE_CASH / ORDER_REJECTED); ESMA close-out (single position at the exact trigger or the gapped open; several positions largest-loser-first); insolvency; open cycles kept and marked. `reconcile()` asserts balance = start + Σ ledger at every bar and final equity = start + Σ cycle net + NBP.
- `bot/research/fx_survival_study.py` + `manage.py run_dca_survival_study` — baseline reproduction, decomposition, fixed-capital runs, 13 stress scenarios, 5-pair shared-account portfolio, event windows → `research_data/dca_survival/2026-10-16_0800/` (report.json + per-run ledger/cycle/fill/event/row CSVs).
- `bot/mt5_data_fetcher.download_mt5_spread_history()` → `data/forex/spreads/*_1h_spread.csv` + `broker_cost_snapshot_2026-10-21.json`.
- `bot/research/fx_market_events.json` — 9 sourced events (URLs, publishers, dates); annotation only.
- Dashboard: capital-survival section on `/execution/dca-engine/` (`templates/partials/_dca_survival.html`) + per-cycle ledger endpoint `/execution/dca-engine/ledger/`; legacy section kept and labelled.

Defects found and fixed during the build (by tests or independent checks): add-on lots floored independently gave a 1:2:5:10:22 ladder → now exact multiples of the executed entry; rollover times in µs vs ns (swap silently 0); the delayed-execution scenario froze the pair (zero trades); same-bar look-ahead in the free-margin check; multi-position close-out closed everything at once (now ESMA largest-loser-first).

## 9. Results — GBP/JPY, 2022-08-10 → 2026-10-16 07:00

**Decomposition (what each correction changes)**

| Step | Net P&L | Max DD — $ (% of peak cumulative P&L; no account base) | DD basis |
|---|---|---|---|
| S0 legacy page at the frozen cutoff | +$8,116 | −$3,487 (−51.9 %) | closed trades |
| S1 continuous replay, exact $3k notional, no costs, unlimited capital | +$8,066 | −$5,761 (−86.9 %); intrabar −$6,396 (−96.4 %) | MTM equity |
| S2 + broker lot grid (entry ≈ 0.02 lots; ladder 0.02→0.32) | +$6,812 | −$4,925 (−89.4 %) | MTM |
| S3 + MT5 spread history | +$5,621 | −$4,967 (−100.6 %) | MTM |
| S4 + swap (current broker rates; long = credit) | +$6,856 | −$4,920 (−89.3 %) | MTM |

The legacy figure in this morning's screenshot (+$9,524.55) used data up to 2026-10-20 with fold boundaries that moved every hour; at the frozen cutoff the same code gives +$8,116.

**Fixed capital (S5, ESMA)**

| Capital | Ending equity | Net | Max DD — $ (% of peak equity) | Close-outs | Notes |
|---|---|---|---|---|---|
| $3,100 | $6,483 | +$3,383 | **−$4,492 (−90.7 %)** | **1** (2022-09-26, −$2,362) | 19 add-ons refused for margin; min margin level 67 % |
| $5,000 | $11,856 | +$6,856 | −$4,920 (−65.4 %) | 0 | min margin level 91 %; 1 margin call |
| $10,000 | $16,856 | +$6,856 | −$4,920 (−34.7 %) | 0 | capital never binds |

Costs: spread $729 = 2.5 % of gross positive P&L; swap credit +$1,236 exceeds costs (profit retention 108 %). Closed-cycle win rate 82 %; gross profit factor 1.27. Worst floating loss −$3,637; worst realised cycle −$3,072 (2024-07-30 → 08-02). Longest underwater spell ≈ 419 days.

**Shared account, 5 QC pairs**

| Capital | Ending equity | Close-outs | Outcome |
|---|---|---|---|
| $3,100 | $40 | 5 | **insolvent**; DD −$3,077 (−98.7 %) |
| $5,000 | $185 | 4 | **−96 %**, effectively wiped out; DD −$7,948 (−98.1 %) |
| $10,000 | $16,416 | 1 | survived; DD −$11,580 (−73.8 %); NZD/CHF −$5,774, EUR/USD −$834 |

**Stress (GBP/JPY).** At $5,000 no scenario liquidates; the combined realistic-retail case (spread ×2, 0.5 pip slippage, $3.5/lot/side, no swap) cuts the result to $7,745 (+55 %). At $3,100 most scenarios cause 2–7 close-outs, and the combined case ends at **$43 (insolvent)**. A hypothetical −5 % gap at peak exposure costs ≈ $4,445 at every capital level.

**Events.** The largest losses and the only GBP/JPY close-out coincide with sourced events: UK mini-budget / sterling record low (Sep 2022: −8.3 % adverse, ladder exhausted, margin call), BoJ hike + carry unwind (Jul–Aug 2024: −9.0 %, −$3,072 realised), BoJ YCC band change (Dec 2022), US tariffs (Apr 2025), yen rebound on BoJ signals (Sep 2026). Coincidence, not proof of cause.

**Reading.** The strategy wins 82 % of cycles but its risk is concentrated in a handful of yen/sterling shocks. Survival depends on capital: the account that exactly funds the ladder ($3,100) was liquidated in its 7th week, and the five-pair portfolio is destroyed below $10,000. About 18 % of the GBP/JPY result depends on a swap credit estimated from today's rates, and commission is unknown. Nothing here is out-of-sample evidence of an edge.

## 10. Tests

- `bot/tests/test_fx_survival_replay.py` — 24 tests: accounting identities, exact P&L, spread on buys only, slippage both sides, commission per fill, swap rollovers (×3 Wednesday), WAP, zero-denominator ratios, 1:2:4:8:16 ladder and exhaustion ≠ liquidation, margin rejection, open-at-end, minimum volume, execution delay, legacy equivalence, close-out at trigger / gap / negative-balance protection + insolvency / largest-loser-first, ESMA leverage, drawdown basis, seal cutoff refused, determinism, no look-ahead, real GBP/JPY reconciliation.
- Existing DCA/FX modules: 132 tests OK after the `decide_in_cycle` refactor. Pre-existing failure, not caused here: `test_dca_phase2...test_fx_entry_points_report_fx_calendar_unavailable`.
- **Full suite** (`manage.py test bot --noinput`, 2026-10-21): 2,321 tests in 7,179 s — 2,300 passed, 6 failures, 13 errors, 2 skipped. **All 19 are pre-existing**: re-running exactly those tests with every tracked change of this work stashed gives the same 6 failures + 13 errors. They are: 13 errors from `AVA_USDT/PHA_USDT` missing in `research_data/cointegration_pairs.csv` (pairs config/monitor) plus a Kalman seed-candle error; FX now accepted where older tests expect refusal (3 tests — market-agnostic refactor of 2026-10-18); crypto data 24–52 days stale (`fetch_all_symbols.py` not run since 2026-09-24); forex universe not currency-balanced (58 registered pairs vs the 28-pair test assumption); a research-lab byte-identical crypto baseline (0.336 vs 0.3465).
- After the later edits (drawdown %, `ALLOWED_HOSTS`): 319 tests across every touched area — only the pre-existing `test_dca_phase2` failure.

## 11. Commands

```
venv\Scripts\python.exe manage.py test bot.tests.test_fx_survival_replay
venv\Scripts\python.exe manage.py test bot --noinput
venv\Scripts\python.exe manage.py run_dca_survival_study                  # ~4 min -> research_data/dca_survival/<cutoff>/
venv\Scripts\python.exe manage.py run_dca_survival_study --refresh-costs  # re-read spreads + swap from the MT5 terminal first
venv\Scripts\python.exe manage.py runserver                               # then http://127.0.0.1:8000/execution/dca-engine/
```

## 12. Files changed

| File | Change |
|---|---|
| `bot/research/fx_fold_runner.py` | decision block extracted into `decide_in_cycle()` (behaviour identical) |
| `bot/research/fx_survival_replay.py` | **new** — account/ledger/margin replay engine |
| `bot/research/fx_survival_study.py` | **new** — study runner, stress suite, event windows, report |
| `bot/management/commands/run_dca_survival_study.py` | **new** — CLI |
| `bot/mt5_data_fetcher.py` | `download_mt5_spread_history()` (read-only MT5) |
| `bot/research/fx_holdout_guard.py` | `QC2_HOLDOUT_START`, `FX_RESEARCH_DATA_CUTOFF` constants |
| `bot/research/dca_wf_validator.py` | uses `FX_RESEARCH_DATA_CUTOFF` (stricter) |
| `bot/views/dca_engine.py` | frozen cutoff for the legacy builder (cache v8), survival payload, ledger endpoint |
| `bot/urls.py` | `/execution/dca-engine/ledger/` |
| `config/settings.py` | local runs only: `ALLOWED_HOSTS` adds this PC's current LAN IP + hostname at startup (phone access survives DHCP changes); unrelated to the DCA work, requested the same day |
| `templates/dca_engine.html` | header/badge corrected, survival partial included, legacy disclaimer |
| `templates/partials/_dca_survival.html` | **new** — dashboard section |
| `bot/tests/test_fx_survival_replay.py` | **new** — 24 tests |
| `bot/research/fx_market_events.json` | **new** — sourced event dataset (tracked) |
| `data/forex/spreads/*`, `research_data/dca_survival/*` | **new** data / outputs (gitignored) |

Drawdown display rule (owner, 2026-10-21): every drawdown is shown as −$X (−Y %) — % of peak equity for account runs, % of peak cumulative P&L where there is no account (S0–S4; can exceed 100 % when a drawdown erases more than the profit accumulated so far).

## 13. Known limitations

1 h bars (intrabar order unknown; close-outs checked at bar extremes); the MT5 bar spread is typically the bar minimum and often 0 (optimistic); swap from today's rates applied to 2022–2026 (estimate); commission unknown; the ESMA rule approximates a broker's engine (the real account is 1:100 / 50 %); portfolio close-out assumes the pairs' extremes coincide; the entry is floored to 0.01 lot (≈ $2.4–2.8k, not exactly $3,000); no partial fills or depth; in the portfolio the EUR/USD leg starts in 2021-08 on its own walk-forward schedule, the other pairs in 2022-08; results are descriptive — strategy and pairs were chosen after earlier looks at this data (CRRP-2026-10-02).
