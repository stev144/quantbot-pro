# Futures Funding Intelligence and Leverage Reality — review package (2026-10-22)

Brief: "STEPH QUANT TECHNOLOGIES — Staged Implementation: Futures Funding Intelligence and Leverage Reality".

**Mode:** research and education only.
- No order, exchange credential, live endpoint or `DRY_RUN` change.
- QC-1, QC-2 and every sealed window untouched.
- **Not committed.**

## 1. Audit (Step 1)

### Five gates
`FULL_RESEARCH_PIPELINE_FIVE_GATE_AUDIT_2026-10-03.md` §4 holds the wording quoted in the brief. Each phase is
judged against it below.

### Protected periods: how they relate
- **Crypto:** `bot/quant_core/research_zones.py` defines `ZONES.F` = **2026-10-04 00:00 UTC**.
  - It was sealed by owner decision D-8.
  - It is evaluated once, at 2027-04-04.
  - All earlier crypto history is PSEUDO_HOLDOUT (not claim-eligible).
- **FX:** `FX_RESEARCH_DATA_CUTOFF` = 2026-10-16 08:00 UTC, the minimum of v7 A6-3 and QC-2.
- **QC-2:** the EUR/CAD FX holdout, 2026-10-19 → 2027-01-17.
- The FX seals and QC-2 cover forex only. This work is crypto-only, so **F governs it**.
- **Recorded data used:**
  - Funding files end 2026-09-19 or earlier (none reaches F).
  - Spot files had their post-F rows moved to the sealed store.
  - Every reader still splits and asserts against `ZONES`.

### Existing components reused (single sources of truth)

| Need | Existing source reused |
|---|---|
| Funding sign, coverage, cash flow | `bot/quant_core/funding.py`: `normalise_funding`, `funding_cost`, `load_funding_series`, `MAX_FUNDING_GAP`. Positive funding means longs pay shorts; a gap over 8 h makes a window unavailable; a rate is never imputed as 0. Already tested by `test_perp_funding` (10 tests) and the Deep Health L1 `funding` check. |
| Recorded data | `data/<SYM>_funding.csv` (Binance `fundingRateHistory` via `bot/engines/derivatives_data.py` / `bot/fetch_derivatives_data.py`): 119 files. Mark price is recorded only from 2023-10-31. `data/<SYM>_1h.csv` spot candles from 2021-09-26. |
| Seal | `ZONES.split_sealed`, `ZONES.assert_no_sealed_data` |
| Fees | `bot/config/execution_costs.py`: `FEE_RATE` 0.10 % spot, `SLIPPAGE_RATE` 0.05 %. **New:** `PERP_TAKER_FEE_RATE` 0.05 % (Binance USDⓈ-M VIP 0 taker; an assumption). |
| Provenance | `bot/quant_core/provenance.py`: `make_run_id`, `input_files_fingerprint`, `code_state`, `file_sha256`, `write_research_output_provenance` |
| Uncertainty interval | `bot/quant_core/inference.time_block_bootstrap_mean_ci` (descriptive) |
| Liquidation / leverage maths | **None existed** for crypto. `fx_survival_replay` has only an ESMA FX close-out. |

### Other findings
- **Scratchpad scripts audited** (`mentor_martingale_check.py`, `spot_vs_futures_check.py`):
  - Logic was partly reusable.
  - Defects found: the ladder assumed every step could be funded, which is false for the advertised $1,000 ladder; the in-bar order was not defined; there was no unresolved-outcome class; no interval; no seal assertion; no funding-gap check in the hold.
  - All are fixed in the module.
- **Baseline tests:** `test_perp_funding` 10/10 OK before any change. The earlier full run was 2,358 OK (2 skipped) at the pre-card state.
- **Pre-existing issue (not touched):** local DB row `LearningPath` pk 14 has an empty slug and title, so `/academy/paths/` raises `NoReverseMatch`. It is a data problem, not a code regression from this work.

## 2. Phase 1: Funding-Carry Monitor

### Position modelled
A hypothetical $10,000 **long spot + short perpetual**:
- The same quantity of base asset on both legs: Q = 10,000 / spot price at the entry settlement.
- Held from the first to the last recorded settlement that has a ≤ 1 h spot price.

### Formulas
Derived in the module header; G2 known-answer tests are in brackets.
- **Funding to the short:** F = Σ r_k·Q·S_k = −`funding_cost(Q, …)`.
  - S_k is the spot close **at the candle close time**. Indexing by open time would be a one-hour look-ahead; a test catches it.
- **Costs:** Q·S·(f_spot + s) + Q·P·(f_perp + s) at entry and at exit [`round_trip_costs` 1.054].
- **Basis P&L:** Q(S1 − S0) − Q(P1 − P0) [basis known answer and the zero-basis invariant].
- **Net, funding-only (scope A):** F − costs. Basis is **excluded**, and the output says so.
- **Net, with basis (scope B):** F + B − costs.
  - Runs from the first settlement with a recorded mark price (2023-10-31).
  - The mark price is a fill **proxy** and is labelled as one.
- **Capital at short leverage L:** spot leg + Q·P0/L. **Margin is capital, never a cost** (a test asserts net is independent of L).
- **Theoretical isolated-short liquidation:** P0(1 + 1/L)/(1 + m). The defining identity is tested.
- **Annualised:** only for ≥ 365 days, as N/K × 365.25/days, labelled an extrapolation [boundary tests].

### Data handling
Each case either fails closed or is counted.
- **Funding values:**
  - Non-finite rate or malformed timestamp: the asset is **INVALID**.
  - Conflicting duplicate: **INVALID**.
  - Identical duplicate: dropped and counted.
- **Months:**
  - A funding gap over 8 h, or a settlement without a ≤ 1 h spot price: the month is **INCOMPLETE**, excluded and listed, never zero-filled.
- **Excluded rows:** sealed, invalid and duplicate rows are counted per asset.
- **Mark prices:**
  - A mark price not in spot units is refused by a unit guard; ratio bounds are 0.8–1.25.
  - This caught SHIB, whose perpetual is quoted per 1,000 SHIB: its basis would have been +$3.06 M.

### Results
Run `8c27b4bbf5598d62…`, module `00b988961e57`. Descriptive only.
- **Coverage:** 118 of 119 assets. MATIC has no spot/perp overlap (POL rebrand).
- **Funding-only net:**
  - The median asset made **+8.9 % of notional over about 5 years**. That is the whole period, not per year.
  - **33 assets were negative.**
  - Median share of negative months: 23 %.
- **With basis (115 assets, from 2023-10-31):** median +24.1 %.
- **2× short:** **79 %** of assets reach the theoretical liquidation price without margin top-ups.

| Asset | Gross funding | Costs | Net (funding-only) | Worst month | Negative months | 2× ≈ /yr (extrapolated) | Collateral if topped up monthly |
|---|---|---|---|---|---|---|---|
| BTC | +$5,297 | −$74 | +$5,223 | 2026-04 −$33 | 7 % | 7.0 % | 54 % of notional |
| ETH | +$3,582 | −$49 | +$3,534 | 2022-09 −$91 | 8 % | 4.7 % | 75 % |
| SOL | +$2,264 | −$47 | +$2,216 | 2022-11 −$431 | 28 % | 3.0 % | 171 % |
| TRB | **−$22,316** | −$36 | **−$22,353** | 2023-12 −$10,661 | 13 % | −29.9 % | 712 % |

**Main conclusion:** the funding cash flow is real but small. It is negative for a quarter of coins, and it can
collapse during squeezes (TRB). The capital needed to keep the short alive is often a large multiple of what
funding earns.

### Persisted outputs
- `research_data/funding_carry/funding_carry_report.json` and `funding_carry_monthly.csv`, each with a provenance sidecar.
- The UI reads these files only.
- They are produced by `python manage.py run_funding_carry_monitor`.
- Nothing is scheduled; there is no background process.

### Page
`/research/funding-carry/`, under Research → Funding Carry Monitor.
- **Top of the page:** banner "Historical research estimate — not a live yield or trading recommendation", plus four summary tiles.
- **Per-asset table:** period, settlements, gross, costs, both nets, worst month, negative months, capital at 2×, net/capital, data date.
- **Per-asset "open" panel:** quantity, price source, costs split, scope B, capital and liquidation for 1–3×, monthly series, cost sensitivity, sources, excluded rows.
- **Bottom of the page:** limitations list and provenance line.

### Gate matrix: Phase 1

| Gate | Verification | Evidence | Status | Remaining limitation |
|---|---|---|---|---|
| G1 Software | Validation, fail-closed states, timestamps, duplicates, gaps, staleness, no silent fallback; the page never computes | `test_funding_carry_monitor` 21 tests (CashFlow, Completeness, Seal/Provenance, Page) | PASS | Full-suite result: §5 |
| G2 Mathematics | Funding sign, units (base qty × USD price), costs, basis, liquidation identity, annualisation | Known-answer tests: $208 gross, $158 net, 1.054 cost, P_liq identity | PASS | — |
| G3 Statistics | No inferential claim; everything is labelled DESCRIPTIVE; annualising is restricted to ≥ 365 days and labelled as extrapolation | Report `classification`; page banner | PASS for the claims made (none inferential) | No uncertainty interval on per-asset funding totals; none is claimed |
| G4 Quant / trading | Causal close-time pricing; real settlement stamps; fees and slippage on both legs; margin separated; liquidation exposure shown; incomplete months excluded | Timing test; capital tests; unit guard | **PARTIAL** (disclosed) | Mark-price proxy for fills; no rebalancing trades; flat maintenance rate; spot highs instead of mark; no borrow or counterparty risk |
| G5 Research integrity | Run ID from config, data fingerprint, zones hash, commit, dirty digest and **module sha256** (fixes an untracked-file gap); sidecars; seal tests; governance log | `SealAndProvenanceTests`; governance entry 2026-10-22 | PASS | Report generated with a dirty tree (uncommitted work); regenerate after any commit |

Phase 1 found no blocking defect, so Phase 2 proceeded.

## 3. Phase 2: Leverage Reality

### Calculator
`ladder_scenario`, linear USDⓈ-M contracts only. Inverse contracts are refused.
- **Liquidation price:** the position is liquidated when B + d·Q·(P − A) = m·Q·P.
  - Long: (QA − B)/(Q(1 − m)).
  - Short: (QA + B)/(Q(1 + m)).
  - B is the position margin (isolated) or the account equity after fees (cross).
- **Tests confirm:**
  - long and short identities;
  - a single isolated step equals the hold formula;
  - cross is never closer than isolated;
  - the 4-step $2,000 ladder gives exact averages, notional, fees and liquidation price.
- **Funding rule:** a step fills only if the **available balance** covers its margin and fee. Available balance = wallet − fees − margin in use + unrealised *loss* in cross margin.
  - The advertised **$1,000 / 100-200-300-400 ladder stops at step 4** ("needs $400 + $4 fee, $195 available").
- **The ladder also stops** when the liquidation price would be reached before the next step fills.
- **Input validation:**
  - 1–125×; 1–10 steps; spacing 0–50 %.
  - Positive amounts; finite numbers.
  - Maintenance rate < 50 %; fee < 1 %.
  - Invalid input is reported, never adjusted.

### Historical data
Descriptive. Run `753c3d9b708a…`, module `b6568865774c`. 161 spot files, all before F.

**Definitions:**
- **WIPEOUT:** at the bar's extreme, equity ≤ maintenance; the whole backing is lost.
- **TAKE_PROFIT:** filled exactly at the take-profit level.
- **UNRESOLVED:** neither happened within 30 days; reported, never counted.

**Method:**
- Two in-bar orderings bound the result.
- Steps fill exactly at their level, subject to the funding rule.
- Ladder: funding is **excluded**. Hold: funding **included** from the recorded series, and a window crossing a funding gap is skipped.
- Spot candles stand in for the mark price.
- Intervals are a 30-day time-block bootstrap (descriptive).

**Warnings shown on the page:** not a forecast; survivorship; overlapping windows.

**Ladder: $1,000 cross margin, take profit +1 % over the average entry (145,962 cycles per configuration)**

| Ladder | Wiped out (of resolved cycles) | One wipeout per | Average result per cycle |
|---|---|---|---|
| 20×, 1 % gap | 3.2 – 6.0 % | 17 – 31 cycles | −$14.53 … +$16.88 |
| 20×, 2 % | 2.1 – 4.3 % | 24 – 47 | −$8.18 … +$13.57 |
| 50×, 1 % | 8.5 – 13.6 % | 7 – 12 | −$42.46 … +$23.17 |
| 50×, 2 % | 5.4 – 8.3 % | 12 – 18 | −$18.66 … +$15.62 |

**30-day isolated long held without a stop (175,857 windows)**

| Leverage | 1× | 3× | 5× | 10× | 20× | 50× |
|---|---|---|---|---|---|---|
| Liquidated | 0 % | 20.6 % (15.6–26.3) | 45.6 % (37.8–52.9) | 72.7 % (67.1–78.1) | 87.6 % (84.3–90.7) | 96.2 % (95.1–97.2) |
| Median result on margin | −5.7 % | −18.9 % | −54.9 % | −100 % | −100 % | −100 % |
| Made money | 39 % | 38 % | 35 % | 23 % | 12 % | 4 % |

**Persisted:** `research_data/leverage_reality/historical_summary.json` with a sidecar, produced by
`python manage.py run_leverage_reality`. The page only reads it.

### Academy page
`/academy/leverage-reality/`. It is public, like the curriculum list, and holds no per-user data. It is linked from:
- the Academy paths list (a separate data issue breaks that page; see §1);
- the Academy dashboard.

The page contains:
- **Definitions:** equity, margin, notional, liquidation.
- **A GET-form calculator** that works without JavaScript, with:
  - a step table;
  - the stop message;
  - an adverse-move sensitivity row.
- **Explanations:**
  - why the liquidation price is not your exit;
  - win rate versus tail risk;
  - funding (linked to the monitor).
- **The historical tables,** with warnings and the classification.

### Gate matrix: Phase 2

| Gate | Verification | Evidence | Status | Remaining limitation |
|---|---|---|---|---|
| G1 Software | Validation of every input; fail-closed messages; deterministic; the page never recomputes history | `test_leverage_reality` 20 tests (formula, ladder, validation, cycle definitions, seal, persistence, page) | PASS | Full-suite result: §5 |
| G2 Mathematics | Liquidation identities (long, short, isolated, cross), ladder averages, fees, the isolated hold formula | Known-answer tests | PASS | Flat maintenance rate (theoretical) |
| G3 Statistics | Shares labelled descriptive with time-block intervals; unresolved cycles separated; both in-bar bounds shown; "not a forecast" warning | `historical_summary.json`; page | PASS for the claims made (descriptive) | Overlapping windows and cross-coin dependence handled only by 30-day blocks; survivorship |
| G4 Quant / trading | Funding rule matches the exchange's available-balance logic; conservative/optimistic bounds; funding in holds; gaps skipped | Cycle tests; audit finding on step 4 | **PARTIAL** (disclosed) | Spot instead of mark; no tiered margins, liquidation fee, ADL or slippage at liquidation; ladder excludes funding |
| G5 Research integrity | Run ID including the module sha256; inputs fingerprint; zones hash; sidecar; seal test; governance log | `HistoricalRunTests`; governance entry | PASS | Generated with a dirty tree; regenerate after commit |

## 4. Files

### New
| File | Purpose |
|---|---|
| `bot/research/funding_carry_monitor.py` | Phase 1 engine |
| `bot/management/commands/run_funding_carry_monitor.py` | Explicit Phase 1 run |
| `bot/views/funding_carry.py` | Read-only Phase 1 view |
| `templates/funding_carry.html` | Phase 1 page |
| `bot/tests/test_funding_carry_monitor.py` | Phase 1 tests |
| `bot/research/leverage_reality.py` | Phase 2 engine |
| `bot/management/commands/run_leverage_reality.py` | Explicit Phase 2 run |
| `bot/academy/views/leverage_reality.py` | Academy view |
| `templates/academy/leverage_reality.html` | Academy page |
| `bot/tests/test_leverage_reality.py` | Phase 2 tests |
| this document | Review package |

### Changed
| File | Change |
|---|---|
| `bot/config/execution_costs.py` | + `PERP_TAKER_FEE_RATE` (additive; nothing existing changed) |
| `bot/urls.py` | + import and route `research/funding-carry/` |
| `bot/academy/urls.py` | + import and route `leverage-reality/` |
| `templates/partials/_topbar.html` | + Research menu link and active slug |
| `templates/academy/path_list.html`, `templates/academy/dashboard.html` | + links to the tool |
| `research_data/model_governance_log.md` | + entry |

### Markers and exceptions
- New Python and test files carry a `# claude code changed: new file` header (project convention) plus inline markers on later fixes.
- New templates carry `<!-- # claude code changed … -->` headers.
- Edited template lines carry `{# claude code changed … #}` or `<!-- # claude code changed -->`.
- **Exceptions, where inline markers would be unsafe or meaningless:**
  - generated JSON and CSV outputs under `research_data/` (gitignored);
  - this Markdown document.

## 5. Tests

| Command | Result |
|---|---|
| `manage.py test --keepdb bot.tests.test_funding_carry_monitor` | **21 OK** |
| `manage.py test --keepdb bot.tests.test_leverage_reality` | **20 OK** |
| `manage.py test --keepdb bot.tests.test_funding_carry_monitor bot.tests.test_perp_funding bot.tests.test_topbar_navigation bot.tests.test_deep_health_gates` | **78 OK** (Phase 1 plus related existing suites) |
| `manage.py test --keepdb bot` (full suite after both phases) | **Pending at the time of writing.** Result reported in the conversation. |

## 6. What this does and does not support
- **Supports:**
  - Historical funding cash flows for a hedged position, with costs, capital and liquidation exposure shown separately.
  - Theoretical, explained leverage arithmetic.
  - Descriptive history of how often leveraged positions and the advertised ladder were wiped out.
- **Does not support:**
  - Any claim that funding carry is a yield, safe, or will persist.
  - Any claim that a leverage level or ladder is acceptable.
  - Exchange-exact liquidation prices.
  - Statistical evidence of an edge.

**Deferred:** Option 3 (partial-take-profit / break-even DCA). Nothing here touches the active DCA work.
