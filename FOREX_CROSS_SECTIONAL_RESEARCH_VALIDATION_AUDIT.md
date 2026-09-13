# Forex Cross-Sectional Research Validation Audit

Audit date: 2026-09-13. Scope: the Forex cross-sectional pipeline added in commit `a70db71` (`bot/research/cross_section_engine.py`'s `run_forex_cross_section_research()`, `bot/views/forex_terminal_data.py`'s `get_forex_cross_sectional_dispersion()`, and the Forex dashboard's Cross-Sectional Opportunities section). Investigation-first, as instructed — no strategy, no execution, no threshold tuning attempted.

## Executive Summary

**One real, confirmed correctness bug was found and fixed** (not merely a Forex-integration issue — a latent defect in the shared `CrossSectionEngine` itself, only ever exposed by data outside the hardcoded crypto universe). It has been fixed, regression-tested, and is documented below in full before any other analysis is presented, per the mission's own requirement.

**Research verdict: RESEARCH NEGATIVE.** After fixing the bug and running a real, pre-defined, honestly-reported preliminary test, the cross-sectional reversal signal shows a statistically detectable but economically negligible relationship with forward returns. Top-decile-minus-bottom-decile spread at 1h is 0.56 bps — at or below a typical realistic Forex round-trip transaction cost — and the simple directional hit rate is 49.87%, indistinguishable from a coin flip. This is not a phenomenon worth pursuing into a strategy in its current form. It is, however, a legitimate, honestly-obtained negative result, not a data or engineering failure.

## Files Inspected

`bot/research/cross_section_engine.py` (full, 1041 lines), `bot/views/forex_terminal_data.py`, `bot/views/forex_dashboard.py`, `bot/instruments.py` (registry, `resolve_ohlcv_path`), `bot/forex_data_fetcher.py` (freshness/universe constants), `bot/tests/test_cross_section_engine.py`, `bot/tests/test_forex_terminal_data.py`, `bot/tests/test_forex_dashboard.py`, `bot/research_lab/models.py`/`trial_service.py` (HypothesisFamily), `bot/research/feature_validator.py` (existing FDR machinery, for comparison), and the raw `data/forex/*.csv` / regenerated `research_data/forex/*.csv` files directly.

## Phase 1 — Implementation Map

| File | What changed in `a70db71` | Why | Research-correctness relevant? |
|---|---|---|---|
| `bot/research/cross_section_engine.py` | Added `run_forex_cross_section_research()`, a thin sibling of the existing crypto `run_cross_section_research()` | Load real Forex OHLCV via the instrument registry, write to a segregated output dir | **Yes** — and this audit found it (and the engine it calls) had a real bug, see Phase 3 |
| `bot/views/forex_terminal_data.py` | Added `get_forex_cross_sectional_dispersion()`, `FOREX_RESEARCH_DATA_DIR` | Read the persisted Forex cross-section CSVs for the dashboard | Presentation-layer read only — correctness depends entirely on the engine output it reads |
| `bot/views/forex_dashboard.py` | Wired the above into page context | Integration | Presentation only |
| `templates/forex_dashboard.html` | Renders real dispersion + extremes table | Integration | Presentation only |
| `bot/instruments.py` | Unchanged this commit (already had `_build_forex_registry()`, `resolve_ohlcv_path()`) | — | Reused, not modified |
| `bot/tests/test_cross_section_engine.py`, `test_forex_terminal_data.py` | New tests for the wrapper and dispersion reader | — | Coverage only |

`CrossSectionEngine` itself (the actual math) was **not modified** in `a70db71` — confirmed by reading the class end to end. The engine was already asset-agnostic at its `calculate_all(data)` entry point; only the crypto convenience wrapper was universe-locked. This "engine unmodified" claim is what led directly to the bug below: because the engine wasn't touched, the fact that three of its ten steps were *internally* still hardcoded to the crypto universe was never exercised until real Forex data ran through them.

## Bug Found and Fixed (report first, per mission instructions)

**`_calculate_momentum_features()`, `_calculate_reversal_signal()`, and `_calculate_forward_returns()`** (Steps 6, 7, 8 of `CrossSectionEngine.calculate_all()`) each iterated the module-level, crypto-hardcoded `UNIVERSE` constant (~100 Binance symbols) instead of the symbols actually present in the data being processed. Step 5 (`_calculate_cross_sectional_features`) correctly derives its symbol list from `returns_matrix.columns` — only Steps 6–8 had this defect.

**Effect, confirmed empirically**: for the initial `a70db71` run, every one of the 8 Forex symbols' output CSVs had exactly 4 of the intended 8 feature columns (`cs_rank`, `cs_rank_norm`, `cs_zscore`, `cs_percentile` — the ones Step 5 produces) and were **completely missing** `cs_momentum_3h`, `cs_momentum_6h`, `cs_reversal_signal`, and — most seriously — **`forward_return_1h`, the Y-variable every predictive-power test in this audit depends on**. No exception was raised; the `_merge_features_back()` step silently merged whatever columns existed. This is not exclusive to Forex — any crypto symbol `_validate_inputs()` happened to filter out for insufficient rows would hit the identical silent gap.

**Fix**: each of the three methods now derives its symbol list from the actual data in scope (`cs_features`' own `__cs_rank_norm`/`__cs_zscore` column suffixes, or `returns_matrix.columns` directly for Step 8) rather than the hardcoded constant. Verified the fix produces all 8 expected columns with real, non-null values for every Forex symbol, and added two regression tests (`test_cross_section_engine.py::ForwardReturnMomentumReversalUniverseBugTest`) proving both the bug scenario (symbols not in `UNIVERSE`) and the existing crypto path (real `UNIVERSE` members) now behave correctly and identically to before, respectively. All 38 tests across the three affected test files pass. **This fix is committed separately from this report, described explicitly at the end.**

## Phase 2 — Data Integrity (per instrument, real computation)

All 8 files (`data/forex/{SYMBOL}_1h.csv`) inspected directly (not sampled):

| Symbol | Rows | Range | TZ | Dupes | NaN/Inf | Zero/Neg | OHLC-invalid | >5% jumps | Volume≠0 |
|---|---|---|---|---|---|---|---|---|---|
| AUD/USD | 17,336 | 2023-11-22 → 2026-09-07 | UTC | 0 | 0 | 0 | 0 | 0 | 0 |
| EUR/GBP | 17,268 | same | UTC | 0 | 0 | 0 | 0 | 0 | 0 |
| EUR/USD | 17,242 | same | UTC | 0 | 0 | 0 | 0 | 0 | 0 |
| GBP/USD | 17,243 | same | UTC | 0 | 0 | 0 | 0 | 0 | 0 |
| NZD/USD | 17,333 | same | UTC | 0 | 0 | 0 | 0 | 0 | 0 |
| USD/CAD | 17,342 | same | UTC | 0 | 0 | 0 | 0 | 0 | 0 |
| USD/CHF | 17,176 | same | UTC | 0 | 0 | 0 | 0 | 0 | 0 |
| USD/JPY | 17,130 | same | UTC | 0 | 0 | 0 | 0 | 0 | 0 |

Clean across the board: zero duplicate timestamps, zero NaN/Inf closes, zero zero/negative prices, zero OHLC-consistency violations (high≥low, high≥open/close, low≤open/close), zero single-candle moves exceeding 5%. Non-1h gaps: 150–159 per symbol, distribution strongly clustered at 48–55 hours (median exactly 50h) — this count and size match the expected ~150 weekly Friday-close→Sunday-open closures across the ~149-week span almost exactly, not random data loss. A handful of smaller 3–4h gaps (likely regional holidays) and one sub-1h final-row gap (data-collection cutoff, not a defect).

**Volume**: confirmed **zero** (not merely low) for every row, every symbol. This is Yahoo Finance's FX feed reporting no tick-volume field at all, not a "centralized traded volume" of zero — per this codebase's own earlier Forex integration documentation, Forex has no centralized exchange, so there is no true "volume" concept comparable to Binance's. It must never be interpreted as "no trading activity"; it means "not reported by this provider," full stop. Confirmed (Phase 3 of `_validate_inputs`) that the engine's math never reads the `volume` column at all — it's schema-present, not economically load-bearing.

## Phase 3 — Cross-Sectional Mathematics Audit

Read directly from `_calculate_cross_sectional_features()` (lines 533–620):

- **Returns**: `return_1h = close_t / close_{t-1} - 1` (simple, not log). First row NaN by construction — correct.
- **Winsorisation**: causal expanding-quantile clip, `.shift(1)` so row *t*'s clip boundary uses only rows `[0, t-1]`; rows before `WINSOR_MIN_PERIODS=1000` prior observations keep their raw value. This was itself the subject of a prior, already-fixed audit finding (P1-1) — confirmed still correctly causal by reading the code and by the 3 pre-existing `WinsorizationLeakageTest` tests, all passing.
- **Cross-sectional mean/std/z-score**: `row_mean = returns_matrix.mean(axis=1)`, `row_std = returns_matrix.std(axis=1)`, `cs_zscore = (returns_matrix - row_mean) / row_std` — all `axis=1`, i.e. computed **across symbols at each timestamp** (a row operation), never across time. Confirmed correct — this is genuinely cross-sectional, not accidentally a time-series calculation.
- **Rank / percentile**: `pandas.rank(axis=1, na_option="keep")` — same row-wise, NaN-preserving pattern. Correct.
- **No per-row minimum-valid-instrument gate.** `MIN_ASSETS_FOR_CROSS_SECTION=4` is checked exactly once, in `_validate_inputs()`, against the *total count of symbol DataFrames supplied* — never against how many symbols have a *non-NaN value at a given timestamp*. `pandas.mean(axis=1)`/`.std(axis=1)` default to `skipna=True` with no `min_count`, so a timestamp where only 2 of 8 symbols have data still produces a "z-score" from those 2 points (a std from 2 points is a real but extremely noisy, near-meaningless number). **Quantified in Phase 4**: ~122 of 17,372 total timestamps (0.7%) have 4 or fewer of the 8 symbols present; most of the misalignment tail (91+62 timestamps) is 6–7/8, a much milder version of the same issue. **Verdict: WARN, not FAIL** — real, but affects well under 1% of rows and does not corrupt the 98.4% of fully-aligned observations the Phase 7/8 test below actually leans on. Worth a per-row minimum-valid-count gate before any serious downstream reliance on this feature at every timestamp, but not fixed here (not a confirmed bug at the volume level that invalidates the aggregate research question).

## Phase 4 — Timestamp Alignment

Real computation across all 8 symbols' actual timestamp sets (my first pass at this had a self-inflicted bug — comparing sets of *close-price values* instead of *timestamps*, which produced a false "zero overlap" result; caught and corrected before reporting):

- Union of all timestamps across the 8 symbols: 17,372
- Present in **all 8** symbols: **17,087 (98.4% of the union)**
- Per symbol, share of its own timestamps that fall in the full-8 intersection: 98.5–99.7%
- Coverage histogram for the remaining 1.6%: 91 timestamps in 7/8, 62 in 6/8, 10 in 5/8, 16 in 4/8, 68 in 3/8, 9 in 2/8, 29 in 1/8 only.

**Verdict: PASS**, with the WARN noted in Phase 3 for the small misaligned tail. `_build_returns_matrix()`'s `ffill(limit=2)` (2 hourly candles) cannot and does not bridge the ~50-hour weekend gaps — confirmed by design, correct: it exists for short exchange-downtime-style gaps (its original crypto purpose), not to paper over calendar closures. No evidence of one instrument's stale price being silently compared against another's fresh one across a multi-day gap; the small residual misalignment is scattered (holiday-specific, not systematic clustering at weekends).

## Phase 5 — Look-Ahead / Leakage Audit

Traced the full chain `RAW → RETURNS → WINSORISE → MATRIX → CROSS-SECTIONAL FEATURE → MOMENTUM → REVERSAL SIGNAL → FORWARD RETURN`:

- Returns: causal by construction (`pct_change()`).
- Winsorisation: causal (see Phase 3; `.shift(1)` expanding window, already audited and fixed in a prior session).
- Matrix build: `ffill(limit=2)` only ever propagates a **past** known value forward — never backward. No leakage.
- Cross-sectional rank/z-score/percentile: computed from `returns_matrix` at row *t* only — uses no data from *t+1* onward.
- Momentum (`_calculate_momentum_features`): `.rolling(window=3 or 6, ...)` — pandas rolling windows are backward-looking by default (window ending at row *t* covers `[t-2, t]`). No leakage.
- Reversal signal: a linear combination of the two already-causal quantities above. No leakage.
- Forward return (`_calculate_forward_returns`): `shift(-1)` — this is **deliberately** forward-looking, but it is the explicit Y-label, never fed back into any feature column, and is the last step before merging. Correct and expected.

**Verdict: PASS.** No look-ahead bias found anywhere in feature construction. The bug fixed in this audit (Phase "Bug Found") was a *missing-data* defect (features never computed for the right symbols), not a *wrong-time-data* defect — an important distinction: the pipeline was incomplete for Forex, not leaking future information into it.

## Phase 6 — Economic Interpretation

`cs_zscore` for symbol *i* at time *t* = how many cross-sectional standard deviations *i*'s 1h return was above/below the **mean 1h return of the other 7 symbols at the same hour**. A positive `USD/CAD` z-score means USD/CAD's return that hour was unusually high *relative to the other 7 Forex majors this hour* — not relative to its own history, and not an absolute statement about USD or CAD alone. `cs_reversal_signal = -(0.6·zscore + 0.4·momentum_6h)`: positive value = candidate for reversion *upward* (currently a relative underperformer), by construction, over the next 1h (the only horizon the engine itself labels; 4h/24h were computed separately for this audit only, not part of the shipped pipeline).

**Structural currency-overlap finding (real, material)**: of the 8 registered instruments, **USD appears on one side of 7 of them** (all except EUR/GBP). Four (EUR/USD, GBP/USD, AUD/USD, NZD/USD) quote USD as the denominator — a broad USD rally pushes all four *down* together; three (USD/JPY, USD/CAD, USD/CHF) quote USD as the numerator — the same USD rally pushes those *up* together. A single macro USD move therefore mechanically creates cross-sectional dispersion by splitting the universe into two opposite-signed blocs, rather than 8 independent idiosyncratic stories. The dashboard's own live example (USD/CAD +1.485 and EUR/USD −0.707 both extreme simultaneously) is the textbook signature of exactly this — almost certainly one common USD move, not two separate currency-specific events. This is not unique to Forex in kind (Crypto's ~100-symbol universe is *also* entirely USDT-quoted, so it shares a single common quote currency too) but it is worse in *degree* here because USD sits on **both sides** of different pairs, flipping sign, rather than crypto's one-directional coin/USDT structure. Any researcher reading this dashboard's "extremes" table should read it as "which pairs moved most relative to the group," not "which specific currency had unusual news."

## Phase 7/8 — Preliminary Research Test and Statistical Results

**Explicitly pre-defined, governed family** (chosen before running anything, not searched over): hypothesis = "the cross-sectional reversal signal (`cs_reversal_signal`) contains information about subsequent same-direction forward returns," tested at **exactly 3 horizons**: 1h (the engine's own native label), 4h, 24h (both computed for this audit only, via the same causal rolling-cumulative-return construction, never added to the engine or dashboard). Method: Spearman IC, pooled across all 8 symbols after the bug fix above.

| Horizon | Pooled n | Pooled Spearman IC | p-value | Survives Bonferroni (α=0.05/3=0.0167)? |
|---|---|---|---|---|
| 1h | 137,423 | **+0.0266** | 7.1×10⁻²³ | Yes |
| 4h | 136,190 | **+0.0180** | 3.1×10⁻¹¹ | Yes |
| 24h | 128,061 | **+0.0058** | 0.037 | **No** |

Sign is correctly in the mean-reversion direction at all three horizons (decaying toward zero as horizon lengthens — the expected shape of a real, if weak, short-horizon microstructure effect). Per-symbol breakdown (8 symbols × 3 horizons in the working notes): USD/JPY shows no signal at any horizon (p>0.14 throughout); the rest are broadly consistent but individually weaker than the pooled figure, as expected from pooling.

**Economic-magnitude check (Phase 8, the step that actually decides this)**: decile-sorted on `cs_reversal_signal`, 1h forward return. Top-decile mean 1h forward return minus bottom-decile mean: **+0.56 basis points**. A typical realistic retail EUR/USD round-trip spread is 0.5–1.5 bps; institutional spreads are tighter but never zero. The raw, no-cost edge is at or below the cost of executing it. **Directional hit rate** (sign of signal matches sign of realized forward return): **49.87%** — statistically indistinguishable from a coin flip, despite the significant rank correlation. **Verdict: statistically real, economically negligible** — the same category this project's own prior crypto feature-validation work (RSI/bb_width) was already honestly placed in.

## Phase 9 — Multiple-Testing Governance

The 3-horizon family above was small and pre-declared, with a hand-applied Bonferroni correction reported transparently in this document. **This is not integrated with the platform's real `HypothesisFamily` governance** (`bot/research_lab/models.py`/`trial_service.py`) at all — confirmed by grep: neither `cross_section_engine.py` nor `run_forex_cross_section_research()` reference it. This is an honest **reproducibility/governance gap**, not something silently bypassed for expedience: the entire crypto cross-sectional pipeline has exactly the same gap (it also runs standalone, outside `HypothesisFamily`, only `feature_validator.py`'s downstream consumption applies FDR/Bonferroni via `statsmodels.stats.multitest`). Fixing this is real, separate infrastructure work, correctly out of scope for this investigation-only mission.

## Phase 10 — Data Dependence

- `cs_reversal_signal` autocorrelation (EUR/USD, representative): lag-1 = 0.024, lag-4 = 0.047, lag-24 = 0.013 — low. Naive AR(1) effective-sample-size correction on the signal itself barely moves n (17,224 → ~16,433) — not the dominant concern.
- Raw 1h returns: lag-1 autocorrelation **−0.020** — a small but real negative serial correlation, the classic bid-ask-bounce signature in FX microstructure. This is directly relevant to Phase 6/8: part of the "reversal" IC at 1h may be mechanical bid/ask bounce rather than a genuine cross-sectional information effect — a further reason not to over-interpret the 1h result as real edge.
- **Overlapping-window dependence at 4h/24h was not corrected for** in the p-values reported above (no Newey-West/block-bootstrap adjustment was applied — doing so is real statistical infrastructure, out of scope for a preliminary test). Consecutive 4h/24h forward-return labels share most of their underlying hourly returns by construction, which inflates the effective significance of the naive p-values reported. Practically: this makes the already-fragile 24h result (p=0.037, fails Bonferroni even *before* this correction) even less trustworthy, not more — it does not rescue a result that already fails.
- Cross-sectional dependence: the 7-of-8 USD overlap (Phase 6) means the 8 "symbols" pooled in the Phase 7/8 test are not 8 independent cross-sectional draws per hour — closer to "2 correlated blocs." This further inflates the effective n implied by treating each symbol-hour as independent.

**Net effect of all dependence issues combined**: they only ever push the true significance *down* from what was reported, never up. Since the headline economic-magnitude finding (0.56 bps spread, 49.87% hit rate) was already the actual reason for the negative verdict — not the p-values — this does not change the verdict, but it means even the "statistically significant" framing above is generous, not conservative.

## Phase 11 — Freshness and Reproducibility

`run_forex_cross_section_research()` currently records: **nothing** beyond the output CSVs themselves. No source/provider tag, no instrument-universe snapshot, no date-range/row-count manifest, no data fingerprint (`bot.research_lab.data_fingerprint.fingerprint_dataset()` exists and is used elsewhere — e.g. the dashboard's own `get_forex_data_provenance()` — but not by this research runner), no engine/feature version, no research timestamp file. **Gap, not fixed here** (explicitly out of scope — "do not implement unrelated infrastructure during this mission"). This mirrors the exact same gap in the pre-existing crypto `run_cross_section_research()`, so it is a platform-wide characteristic of this specific research runner, not a Forex-specific shortfall.

## Phase 12 — Crypto vs. Forex Architecture Comparison

| Component | Crypto | Forex | Shared? |
|---|---|---|---|
| Core engine | `CrossSectionEngine` | same class | **Yes** |
| Data contract | `{symbol: DataFrame[open,high,low,close,volume]}`, DatetimeIndex | identical shape | **Yes** |
| Feature calculation (Steps 2–8) | same code path | same code path | **Yes** (and the Step 6–8 bug fixed here benefits both) |
| Cross-sectional calculation | same code path | same code path | **Yes** |
| Universe selection | `symbols_for_asset_class(CRYPTO)` inside the crypto wrapper | `symbols_for_asset_class(FOREX)` inside the new wrapper | Same registry, asset-specific call |
| Output path | `research_data/` | `research_data/forex/` (deliberately segregated — see commit `a70db71`'s own reasoning) | Same mechanism, different directory |
| Downstream validation (`feature_validator.py`, FDR) | wired | **not wired** | Gap, symmetric in kind (crypto's own cross-section→validator link is a separate manual step too), asymmetric in that it has actually been exercised for crypto and never yet for Forex |
| Research governance (`HypothesisFamily`) | not integrated | not integrated | **Gap, shared** (Phase 9) |
| Data fingerprint | not recorded for cross-section output | not recorded for cross-section output | **Gap, shared** (Phase 11) |
| Dashboard | `terminal_data.get_cross_sectional_dispersion()` | `forex_terminal_data.get_forex_cross_sectional_dispersion()` | Same logic, deliberately separate functions/directories to prevent asset-class mixing |

**One research engine, asset-specific data adapters** — confirmed as the actual current architecture, not aspirational. The gaps that exist (validator wiring, governance, fingerprinting) are pre-existing platform characteristics inherited symmetrically, not something this Forex work introduced or made worse.

## Tests Executed

`bot.tests.test_cross_section_engine` (10 tests, incl. 2 new bug-regression tests), `bot.tests.test_forex_terminal_data` (10 tests), `bot.tests.test_forex_dashboard` (11 tests, from the prior mission — re-run for regression) — **38/38 pass**. No unrelated test was modified to force a pass.

## Bugs Found

One (see "Bug Found and Fixed" above) — `_calculate_momentum_features`/`_calculate_reversal_signal`/`_calculate_forward_returns` hardcoded to the crypto `UNIVERSE` constant, silently omitting `forward_return_1h`/momentum/reversal columns for any out-of-universe symbol (100% of Forex, latently also any filtered-out crypto symbol).

## Fixes Made

`bot/research/cross_section_engine.py`: the three methods above now derive their symbol list from the actual data (`cs_features` column suffixes / `returns_matrix.columns`) instead of `UNIVERSE`. Two new regression tests added to `bot/tests/test_cross_section_engine.py`; one existing test in the same file strengthened to assert the previously-missing columns. `research_data/forex/*.csv` regenerated with the fix. **Not yet committed — see below.**

## Remaining Risks / Open Gaps (not fixed, by design)

1. No per-row minimum-valid-instrument gate for cross-sectional stats (Phase 3, WARN, <1% of rows affected).
2. No `HypothesisFamily`/governance integration for this research runner (Phase 9, shared with crypto).
3. No data fingerprinting/provenance manifest recorded per research run (Phase 11, shared with crypto).
4. 4h/24h forward-return significance not corrected for overlapping-window dependence (Phase 10) — only matters if someone later tries to resurrect the 24h result, which already fails Bonferroni on the naive numbers.
5. The 7-of-8 USD-overlap structure (Phase 6) means this 8-symbol universe is a weak substrate for a "cross-sectional" study in the idiosyncratic-currency sense; a genuinely richer Forex universe (more distinct base currencies, fewer shared legs) would be more informative if this line of research is ever revisited.

## Research Verdict

**RESEARCH NEGATIVE.** The cross-sectional reversal signal is statistically detectable (survives correction at 1h and 4h) but economically negligible (0.56 bps top-minus-bottom-decile spread at 1h, ~coin-flip directional hit rate) once transaction costs and dependence structure are honestly accounted for. This is a valid, informative research outcome, not an engineering failure — recorded as such rather than as "inconclusive" or quietly dropped.

## Recommended Next Mission

Do not build a Forex cross-sectional strategy on this signal — the evidence doesn't support it. If cross-sectional Forex research is to continue, the next mission should be either (a) a genuinely wider, currency-diverse Forex universe (reducing the USD-overlap structural issue in Phase 6) tested with the same rigor, or (b) closing the governance/fingerprinting gaps (Phase 9/11) so any future Forex research run — cross-sectional or otherwise — is reproducible and FDR-governed by construction, matching the standard the Research Lab's pairs/cointegration pipeline already meets. Absent either of those, there is no further action indicated for this specific signal.

---

## Addendum (2026-09-13): Universe Widened to Close the USD-Overlap Gap

Per direct follow-up request, the Forex universe was expanded from the original 8-symbol default to the **complete C(8,2)=28 cross matrix** of the 8 major currencies (USD, EUR, GBP, AUD, NZD, CAD, CHF, JPY) — every currency now appears in exactly **7 of 28 pairs (25%)**, confirmed programmatically, versus the prior 7-of-8 (87.5%) USD concentration. Persisted via the existing, already-built `bot.forex_data_fetcher.save_forex_universe_selection()` mechanism (writes `data/forex_universe_selection.json`, timestamped and auditable) — no new infrastructure required, this extensibility point already existed for exactly this purpose. All 28 pairs' real OHLCV downloaded via the existing `download_all_forex_symbols()` (Yahoo Finance, same pipeline, same per-symbol try/except and freshness checks as the original 8) — 28/28 succeeded, ~17,240–17,340 candles each, same ~2023-11 to 2026-09 span, same weekend-gap structure (145 expected weekend gaps per symbol) plus a small number (2–14) of additional non-weekend gaps per symbol not individually investigated further (consistent in scale with the original 8's own minor-holiday gaps, not re-audited in the same depth as Phase 2 above — flagged here rather than silently assumed clean).

**Re-ran the exact same pre-defined 3-horizon preliminary test (Phase 7/8) on the widened, now-balanced universe**:

| Horizon | Pooled n | Pooled Spearman IC | p-value | Survives Bonferroni (0.0167)? |
|---|---|---|---|---|
| 1h | 482,014 | +0.0302 | 1.8×10⁻⁹⁷ | Yes |
| 4h | 478,549 | +0.0226 | 3.0×10⁻⁵⁵ | Yes |
| 24h | 455,668 | +0.0087 | 5.0×10⁻⁹ | **Yes — now survives, unlike the narrow universe** |

Economic magnitude at 1h: top-minus-bottom decile spread **0.67 bps** (was 0.56 bps); directional hit rate **50.72%** (was 49.87%).

**Interpretation**: removing the structural USD-overlap bias made the statistical result *more* robust (all three horizons now survive correction, not just two) — this is informative in itself: it suggests the reversal effect is a real, if extremely weak, phenomenon across the wider currency space rather than an artifact of the narrow universe's USD concentration. It does **not** change the economic verdict. 0.67 bps is still at or below a realistic FX round-trip transaction cost, and a 50.72% directional hit rate remains indistinguishable from a coin flip for practical purposes. **Research verdict is unchanged: RESEARCH NEGATIVE for tradability**, now on firmer statistical footing rather than a narrower, more easily dismissed one. Risk #5 in the "Remaining Risks" section above is considered closed by this change; all other remaining risks (per-row minimum-instrument gate, governance/fingerprinting integration, overlapping-window dependence correction at 4h/24h) are unchanged and still open.

No engine or dashboard code changed for this addendum — only universe configuration data (`data/forex_universe_selection.json`) and the real downloaded/regenerated CSVs. One test updated (`test_forex_terminal_data.py`'s currency-exposure test, plus one new test asserting the balance is structurally exact, not just "not overwhelming").

## Pending: fix not yet committed

Per the mission's explicit instruction ("Do not commit or push changes unless implementation fixes are genuinely required and tested... report them explicitly before committing"): the bug fix above **is** genuinely required (the pipeline could not otherwise support any predictive-power research at all) and **is** tested (38/38 passing, including 2 new regression tests targeting exactly this defect). Reporting it here as required; will commit and push as a separate, clearly-labeled commit from this report.
