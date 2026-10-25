# Cross-Exchange Research — Phase B: Descriptive Gap Study (2026-10-25)

**Status:** implemented, tested, first run done. **DESCRIPTIVE ONLY.** No orders, no signals, no profitability claim,
no inferential test. Phase C (preregistration) and any execution are **not** implemented.

## 1. Authorisation and governance
- The owner said "continue with phase b" on 2026-10-25. That authorises analysis of recorder data that lies inside the
  sealed crypto forward-holdout window **ZONES.F** (≥ 2026-10-04, D-8).
- Recorded under the strictest reading (owner rule: delegated research decisions take the strictest option):
  1. **descriptive only:** no p-values, significance or edge claims;
  2. **no tuning:** nothing here may select or tune any strategy evaluated on ZONES.F;
  3. **Phase C:** any later inferential test must be preregistered and use only data recorded *after* its registration.
- These conditions are written into every report (`governance` field) and shown on the page.

## 2. What was built
| File | Role |
|---|---|
| `bot/research/cross_exchange/gap_study.py` | Pure measurement (exact Decimal book walk, pair gaps, exclusions), streaming session reader (fixed snapshot of each file's size at start, torn lines skipped, input SHA-256), aggregation (distributions, % positive, episodes), report save (never overwrites) / load |
| `manage.py research_cross_exchange_gap_study` | Manual run: `--session`, `--sizes 1000,10000,50000`, `--max-feed-delay-ms 1000`, `--fee venue=rate` (account tier override) |
| `bot/views/cross_exchange_gaps.py` + `templates/cross_exchange_gaps.html` | Read-only page **Research → Cross-Exchange Gaps** (`/research/cross-exchange-gaps/`) showing the newest saved report |
| Tests in `bot/tests/test_cross_exchange_recorder.py` | 10 new: known-answer walk/gap/fee arithmetic, exclusions, a synthetic session with known shares, episodes and pause handling, depth exhaustion, reproducibility (same run id + input hash), no-overwrite, fail-closed on a missing fee, page states, read-only AST checks; the existing safety tests now scan the new files too |

### Measures
For each instrument and **ordered** venue pair (buy on A, sell on B), all six directions:
- **Top-of-book gross gap** = (best bid on B − best ask on A) / best ask on A, in basis points.
- **Size-dependent gross gap:** the same coin quantity (size ÷ A's best ask) is bought by walking A's recorded asks and sold by walking B's bids.
  - "Insufficient depth" means the recorded top 10 levels can't fill the size. Such a size is counted as insufficient depth, never extrapolated.
- **Estimated net gap:** each leg pays its own venue's taker fee. Net = (sell·(1−f_B) − buy·(1+f_A)) / (buy·(1+f_A)).
- **Quality filters:**
  - both books usable at the same sampling instant;
  - neither row slower than 1,000 ms (`feed_delay_ms`; a negative value means a clock offset, not slowness);
  - exclusions counted per book and reason.
- **Duration:** "episodes" are consecutive one-second instants with the gap above zero.
  - An ineligible instant, or a recorder pause over 2.5 s, ends an episode.
  - Durations are known only to the nearest sample.
- **Clock independence:** instants are grouped by the recorder's monotonic clock, so the 0.9 s wall-clock step at 05:57 UTC (Windows Time fix) does not affect pairing.

### Fees used (base-tier public taker, from `bot/config/execution_costs.py`)
| Venue | Rate | Status |
|---|---|---|
| Binance | 0.10 % | unverified (project assumption) |
| Kraken | 0.80 % | verified 2026-10-22 (Tier 1) |
| Coinbase | 0.60 % | unverified (official page blocked) |

These are not presented as the owner's actual tiers. Use `--fee` to rerun with real account rates.

## 3. First run: session 20261024T211220Z-eac5af
- **Period:** 2026-10-24 21:12 → 2026-10-25 06:53 UTC, about 9.7 h. 34,607 instants; 1.2 % of rows excluded; 3 recorder pauses.
- Report: `research_data/cross_exchange_arb/reports/gap_study_20261024T211220Z-eac5af_20261025T065545.json` (run `81de663cf531ad48`).

| Finding | Value |
|---|---|
| Directions with **any** net-positive instant (any size) | **0 / 24** |
| Best single net instant | **−63.6 bps** (ETH, buy Coinbase → sell Binance) |
| Largest single gross gap | 8.8 bps (ETH, buy Kraken → sell Binance) |
| Typical 99th-percentile gross gap | about 0.5–2.2 bps |
| Combined fees per direction | 70–140 bps |
| Most persistent gross-positive direction | BTC buy Kraken → sell Binance: gross > 0 in 83.8 % of instants, median +0.66 bps, longest run 386 s |

**Depth at 50,000 USDT.** Most directions fall outside the recorded top 10 levels: 97–99 % "no depth" on Coinbase or Kraken legs for BTC, ETH and XRP. The 10-level recording is too shallow to measure large sizes on those venues.

**Reading.** The gross price differences between these three venues are tiny: a few basis points at most and typically below 1 bp. Base-tier taker fees are roughly 30–150 times larger. The persistent Kraken-below-Binance BTC gap (about 0.7 bp) is a level difference, not something capturable at these fees.

This is a description of 9.7 hours of one recording session. It is not a statement about other periods, other fee tiers or maker execution.

## 4. Five-gate assessment (canonical §4)
| Gate | Status | Evidence / reason |
|---|---|---|
| **G1 Software** | **PASS** | 10 new tests plus the 28 existing recorder tests; read-only AST checks; no-overwrite; reproducible run id and input hash |
| **G2 Mathematics** | **PASS** | Known-answer tests for the walk, gross/net and fee arithmetic in exact Decimal; depth never extrapolated |
| **G3 Statistics** | **NOT APPLICABLE** (by design, not a pass) | Descriptive only. Samples are strongly autocorrelated: "% of time" is a duration share, not an independent-event count. No inference is drawn. |
| **G4 Quant / trading** | **PARTIAL** | Executable sides used (ask to buy, bid to sell), size-aware, fees per leg, quality filters. **Not modelled:** inventory and rebalancing between venues, network/withdrawal costs, latency between observation and fills, partial fills and queue position. 10-level depth limits large sizes. Fees are base-tier assumptions, two unverified. |
| **G5 Research integrity** | **PASS (descriptive), with conditions** | Owner authorisation and conditions recorded in each report and in the governance log; immutable raw input (SHA-256); reports never overwritten; classification on every surface |

## 5. Not implemented
- **Phase C:** preregistered hypothesis, power and inference.
- **Fee tiers:** account-specific fee tiers, maker execution modelling, and rebalancing/transfer cost models.
- **Execution:** no order path. It would also conflict with the owner rule that each venue fetches its own data and signal.
