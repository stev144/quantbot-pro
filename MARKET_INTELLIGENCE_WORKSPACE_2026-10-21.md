# Market Intelligence Workspace — implementation report (2026-10-21)

Source brief: "IMPLEMENTATION MASTER PROMPT — AI-Powered Market Intelligence Workspace" (pasted 2026-10-21).
Scope: research, education and market intelligence only. **No order placement, no risk-limit changes, no change to
live trading behaviour.** `DRY_RUN` and every execution safeguard are untouched.

## 1. Audit summary (before any change)

| Area | Found |
|---|---|
| Asset click | `/markets/` cards: crypto → generic dashboard (`/?symbol=`), **forex → nothing** |
| Market data | Binance REST klines (`BinanceKlinesProvider`, live); research CSVs `data/*_1h.csv` (crypto, **sealed at 2026-10-04**), `data/forex/*_1h.csv` (hourly MT5 refresh); MT5 live ticks; MT5 spread history |
| Regime | `bot.engines.regime_detector.RegimeDetector` — "confidence" = indicator agreement (3/2/1 of 3), not a probability |
| Risk | `bot.risk.position_sizer.PositionSizer` (fixed-fractional, cap `MAX_RISK_PCT` 5 %), `bot.config.execution_costs` |
| Research evidence | `bot.research.evidence_registry` (CANONICAL / LEGACY / SUPERSEDED / INVALIDATED / UNVERIFIED) |
| Exposure | `TradeRecord` (status OPEN) — the bot's own journal (0 open rows today) |
| Claude / news | **none**: no SDK installed, no key, no news source code |
| Entitlements | Research-Lab-only subscription service — left untouched; the workspace is free |
| Baseline | 30 related tests + 33 Markets/live-price tests passing; full suite 2,327 tests OK |

## 2. What was built

`/markets/<crypto|forex>/<SYMBOL>/` — e.g. `/markets/crypto/BTC_USDT/`, `/markets/forex/GBP_JPY/`. Opened from every
Markets card (now keyboard-accessible) and from Watchlist symbols.

| Section | Content | Data |
|---|---|---|
| A Overview | price, LIVE / DELAYED / STALE badge, 1h/24h/7d/30d change, source, as-of, bid/ask (FX), session open/closed; asset switcher; 1h / 4h / 1d | Binance 24 h ticker / MT5 tick (live); candles: Binance klines (crypto, forming candle excluded) or MT5 hourly CSV (FX) |
| B Regime | RegimeDetector class, agreement count, ADX, ATR ratio, plain-English meaning, method + "descriptive, not a forecast" | Python |
| C Evidence | 12 metrics (returns, realised vol + rank, EMA spread, trend persistence, range position/high/low, pullback, ATR, lag-1 autocorrelation with 2-SE band, volume ratio) — each with what/sample/meaning/limitation/status | Python |
| D Risk & cost | user-editable scenario (defaults labelled assumptions); bot's own PositionSizer; gross vs net at target/stop; fees/slippage (crypto, configured estimates) or MT5 spread (FX, measured bar-minimum, commission unknown); R:R gross & net; stop in ATR; historical stop-hit frequency | Python |
| E News (AI) | Claude + web search (allowlist) → schema-validated items: OBSERVED FACTS / AI INTERPRETATION / alternatives / research questions; source-linked headline, publisher, time; NEW / UPDATED / ALREADY_ASSESSED / POSSIBLE_DUPLICATE; severity & confidence definitions | Claude API (needs key) |
| F Exposure & alerts | open TradeRecord positions (operators only); informational / review alerts (volatility rank ≥ 80, HIGH_VOLATILITY regime, open position, high-severity AI news); never claims a limit breach | Python + DB |
| G Strategy evidence | stored research results for the asset, each with registry class, result type, sample size, validation status, limitations | research_data/ |
| H Learn | 7 explanations adapted with the asset's measured cost and volatility | deterministic |
| I History lab | pick a past time → see only bars ≤ t → record hypothesis → reveal (signed token) → outcome + what it does / doesn't show; window must end before the sealed holdouts | research CSVs |

## 3. Files

New: `bot/market_intel/{__init__,quant,news_ai,exposure,evidence,lessons,history_lab}.py`, `bot/views/market_workspace.py`,
`templates/market_workspace.html`, `static/js/market_workspace.js`, `bot/tests/test_market_workspace.py`, this report.
Modified: `bot/urls.py` (7 routes, after the watchlist routes), `templates/markets.html` (cards open the workspace;
keyboard access), `templates/watchlist.html` (symbol links). Dependency: **`anthropic` 1.17.0** (+ `pydantic` 2.14.1,
`httpx2`) installed into `venv/`. No migrations.

## 4. Claude API setup

Add to the project `.env` (read server-side by python-decouple; never sent to the browser):

```
ANTHROPIC_API_KEY=sk-ant-...           # required for section E
MI_AI_ENABLED=true                     # default true; false switches the AI off
MI_AI_MODEL=claude-opus-5-5            # default
MI_NEWS_TTL_MINUTES=60                 # shared per-asset cache
MI_AI_MAX_CALLS_PER_HOUR=12            # hard hourly cap on analyses (cost control)
MI_NEWS_LOOKBACK_DAYS=7
MI_NEWS_DOMAINS=reuters.com,ft.com,... # optional override of the publisher allowlist (max 64)
```

Restart the Django server after editing `.env`. Cost per analysis: two Claude Opus 5.5 calls (one with up to 4 web
searches); the cache means at most one analysis per asset per TTL, and the hourly cap bounds the total. Forcing a
refresh requires sign-in. Server-side refusal fallbacks (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`)
are enabled.

## 5. Security and safety

- Key read only in `news_ai.ai_config()`; `public_config()` exposes model/limits only; a test asserts the key never
  appears in any page or JSON.
- Links and headlines come from the API's `web_search_result` blocks / citations, filtered to the allowlist; model
  text cannot introduce a URL. Items citing a non-existent source are discarded (count shown).
- Retrieved text is passed inside `<untrusted_sources>`; both system prompts declare it data, not instructions; the
  only tool offered is Anthropic's hosted web search — no code execution, no app tools.
- Every AI/news string is rendered with `textContent`; links are http(s) only with `rel="noopener noreferrer nofollow"`.
- Inputs validated (asset class, symbol regex + file existence, timeframe whitelist, numeric scenario, horizon range,
  signed history token); per-IP throttle on the news endpoint; POST + CSRF for the reveal.
- Seals: crypto data after 2026-10-04 and FX data after 2026-10-16 08:00 are never read by the history lab or research
  sections; live display prices are memory-cached only, never written to `data/`.

## 6. Tests

`venv\Scripts\python.exe manage.py test bot.tests.test_market_workspace` → **17 tests, OK** (Claude mocked).
Regression set: `manage.py test --noinput bot.tests.test_market_workspace bot.tests.test_markets_live bot.tests.test_new_views
bot.tests.test_topbar_navigation bot.tests.test_market_intelligence_failfast bot.tests.test_regime_precomputer
bot.tests.test_dashboard_stale_fallback` → **72 tests, OK**. Full suite earlier the same day (before this feature):
2,327 tests OK (2 skipped). Pre-existing failures: none outstanding.

## 7. Manual verification

1. Restart the server; open `/markets/`, click a crypto card and a forex card → the workspace opens.
2. Header: LIVE badge for Binance / MT5-tick prices; DELAYED / STALE for saved candles; switch 1h / 4h / 1d.
3. Risk: change stop/target → Calculate → gross vs net and costs; try stop above entry for LONG → validation error.
4. News: without a key → "not configured"; with a key → items with links; open a link to confirm attribution.
5. History lab: pick a date → only past bars; reveal requires choosing up / down / flat first.
6. Signed in as staff → Exposure shows the bot's open positions (none today); as a normal user → operators-only notice.

## 8. Limitations

No historical swap/commission or tick data; MT5 bar spread is the bar minimum (optimistic); FX daily bars use UTC days;
the regime "confidence" is indicator agreement; evidence metrics are descriptive, none validated out of sample; Claude
news quality depends on the allowlisted publishers' coverage and the web search index; LocMemCache counters are
per-process (use a shared cache backend before running several server workers).
