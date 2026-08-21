# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Django project ("Steph Quant Technologies") that is really three things sharing one codebase:

1. **Live trading bot** (`bot/core/bot_runner.py`) — polls Binance via `ccxt`, detects market regime, routes to a strategy, executes/manages positions, journals trades to the DB.
2. **Backtesting/dashboard web app** (Django views + `templates/dashboard.html`) — fetches historical klines, runs the backtester, renders equity curve / stats / trade table.
3. **Offline research pipeline** (`bot/research/*`, `run_research_all.py`) — feature engineering, cointegration/Kalman-filter pair analysis, walk-forward and permutation testing over CSVs in `data/` and `research_data/`.

There is no `requirements.txt`/`Pipfile` — dependencies live only in `venv/`. Key libs: Django 6.0, `ccxt`, `pandas`, `numpy`, `statsmodels`, `scipy`, `requests`, `aiohttp`.

## Commands

Activate the venv first (Windows): `venv\Scripts\activate` (or call `venv/Scripts/python.exe` directly).

```
python manage.py runserver                  # dashboard at http://127.0.0.1:8000/
python manage.py makemigrations bot
python manage.py migrate                    # required after touching bot/journal/models.py (TradeRecord)
python manage.py health_check               # bot/management/commands/health_check.py — system health CLI
python manage.py health_check --verbose --module backtester --limit 200
python manage.py deep_health_check          # 5-layer structural/functional/behavioural/integration/drift audit
python manage.py test bot                   # 190+ tests in bot/tests/ (a package, not the old bot/tests.py stub) — see Testing below
python bot/core/dry_run_test.py             # run BEFORE ever starting the live bot — smoke-tests every component
python bot/core/bot_runner.py               # starts the live loop (defaults to DRY_RUN = True — see below)
python bot/fetch_all_symbols.py             # populates data/*.csv used by the research pipeline
python bot/run_research_all.py              # runs feature calc + validation across all symbols in data/
```

`bot/tests/` (a package — not `bot/tests.py`, which was the old empty-stub module referenced by some historical comments) now holds 190+ real tests covering the multi-venue execution layer (see below), run via `python manage.py test bot.tests`. Convention throughout: **no mocking** — tests hit real public ccxt endpoints (`load_markets()`, `fetch_ticker()`, dry-run fills) rather than mocked exchange objects, since a mocked exchange can't catch a real cross-venue API discrepancy the way a real call can (this convention directly caught several real bugs during development — see Multi-venue execution below). `dry_run_test.py` and the two management health-check commands remain the closest thing to a live/manual smoke-test suite.

## Architecture

### Signal pipeline (shared by live bot and backtester)

Both `bot/core/bot_runner.py` (live) and `bot/backtesting/backtester.py` (historical) drive the **same** pipeline, just with different data sources and execution back-ends:

```
OHLCV DataFrame
   → RegimeDetector.detect(df)         (bot/engines/regime_detector.py)   → RegimeResult(regime, confidence, adx, atr_ratio, ...)
   → StrategyRouter.route(df, regime)  (bot/engines/strategy_router.py)   → signal dict
        TRENDING_UP/DOWN → MovingAverageStrategy   (bot/strategies/moving_average.py)
        RANGING          → MeanReversionStrategy   (bot/strategies/mean_reversion_strategy.py)
        HIGH_VOLATILITY  → no strategy fires
   → ExecutionEngine.execute_signal(signal)   (live only, bot/engines/execution_engine.py)
        or Backtester's own trade simulation  (bot/backtesting/backtester.py)
```

`grid_strategy.py` / `grid_engine.py` exist but are **not** wired into `StrategyRouter` — standalone/experimental, not part of the live routing path.

Signal dicts use a fixed key contract that both sides depend on: `signal` ("BUY"/"SELL"/"NO_SIGNAL", uppercase — lowercased only right before hitting ccxt), `entry`, `sl` (not `stop`), `tp`, `rsi`, `reason`, `strategy`, plus `regime`/`regime_confidence` once StrategyRouter attaches them.

### Live execution stack (`bot/engines/`, `bot/risk/`, `bot/journal/`)

`ExecutionEngine` is the central coordinator, composing:
- `adapter` (`ExchangeAdapter` — see Multi-venue execution below) — `ExecutionEngine(exchange, dry_run=False, adapter=None)` builds a `BinanceAdapter` internally when `adapter` isn't given, so every pre-existing construction site is unaffected; `self.order_manager`/`self.market_data` are borrowed from the adapter, not built directly.
- `OrderManager` — all exchange order placement; raises typed exceptions (`OrderStillOpenException`, `OrderUnconfirmedException`, `RateLimitException`) instead of returning `None`, so callers can react per-failure-mode. Log lines are venue-tagged (`[OrderManager:binance]`/`[OrderManager:kraken]`, via `exchange.id`).
- `PositionTracker` — in-memory dict of open positions (`{symbol: {...}}`), fast checks every candle; `is_reconciled` flag must be true (set by `reconcile_with_exchange()` on startup) before any trade is allowed — this is a hard safety gate in `execute_signal`.
- `MarketData` — live price/candle/balance fetching with its own cache. Same venue-tagged logging as `OrderManager`.
- `PositionSizer` (`bot/risk/position_sizer.py`) — fixed-fractional sizing (default 1% risk/trade, hard-capped at 5%), floors quantity, never sizes up. One instance per `ExecutionEngine` (per venue) — same shared class/policy, independent state, not a second risk engine.
- `TradeLogger` (`bot/journal/trade_logger.py`) — writes `TradeRecord` (Django model) rows on entry/exit, including `venue` (defaults `"binance"`).

`bot/config/execution_costs.py` is the single source for cost constants: `FEE_RATE`/`SLIPPAGE_RATE` (Binance-modeled, consumed by `OrderManager`'s dry-run fill simulation and the backtester's `bot/engines/simulation.py`) plus `VENUE_EXECUTION_COSTS`/`get_venue_execution_costs(venue_id)` (per-venue table — see Multi-venue execution below). `bot/config/risk.py` is the single source for `DEFAULT_RISK_PCT`/`MAX_RISK_PCT`/`MAX_DRAWDOWN_PCT`/`DRY_RUN_PAPER_BALANCE`.

### Multi-venue execution (`bot/engines/exchange_adapter.py` and siblings)

A Kraken integration was added alongside Binance without touching how Binance behaves — every piece below was verified to leave the existing Binance/`bot_runner.py` path unchanged (191 tests, `dry_run_test.py` green throughout). Nothing here is wired into `bot_runner.py`'s live loop yet — it stays single-symbol/single-venue (Binance) by design; the multi-venue capability exists, tested, ready to be activated by a future step, not activated now.

- **`ExchangeAdapter`** (`bot/engines/exchange_adapter.py`) — the venue-agnostic contract (`abc.ABC`; a subclass missing a method fails to instantiate, not a silent gap): `get_ticker`, `get_ohlcv`, `get_balance`, `place_order`, `place_stop_loss`, `cancel_order`, `get_order`, `get_open_orders`, `get_order_book`, `normalize_symbol`, `normalize_quantity`, `normalize_price`, `validate_order`, `get_execution_costs`, `validate_connection`, plus a `venue_id` property.
- **`BinanceAdapter`/`KrakenAdapter`** (`bot/engines/binance_adapter.py`/`kraken_adapter.py`) — each wraps `OrderManager`/`MarketData` (not a reimplementation) with venue-correct cost rates baked in at construction. `KrakenAdapter.place_stop_loss()` is its own implementation, not delegated — ccxt's Kraken `create_order()` docstring calls `stopLossPrice` "margin only" but the code that builds the request applies it unconditionally; unresolved without a live authenticated call, documented inline rather than assumed either way. `build_kraken_adapter()` is an env-var-driven factory (`KRAKEN_ENABLED`, `KRAKEN_DRY_RUN` defaulting `"true"`, `KRAKEN_API_KEY`/`KRAKEN_API_SECRET`) that refuses to construct a live adapter without real credentials.
- **Symbol normalization** (`KrakenAdapter.normalize_symbol()`) — real, verified divergence: 7 of this project's 20 tracked symbols aren't listed on Kraken under `/USDT` at all but are under `/USD` (handled by a fallback); Kraken doesn't list `MATIC` under any quote (Polygon rebranded to `POL`) — returns `None` for a genuinely untradeable symbol rather than guessing.
- **`OrderIntent`/`StopLossIntent`** and **`ExecutionResult`/`RestingOrderResult`** (`bot/engines/order_intent.py`/`execution_result.py`) — typed request/response wrappers around `place_order()`/`place_stop_loss()`, consumed by `ExecutionEngine` itself (not just their own tests) for the entry/exit/stop-loss call sites.
- **Per-venue cost model** (`bot/config/execution_costs.py`'s `VENUE_EXECUTION_COSTS`/`get_venue_execution_costs()`) — Kraken's real fee (0.26%, lowest published tier) vs. Binance's (0.1%); `OrderManager(exchange, fee_rate=..., slippage_rate=...)` accepts overrides (`None` defaults preserve old behavior) so dry-run fill simulation is venue-accurate, not silently Binance's rate regardless of adapter.
- **`get_order_book()`** — normalizes each bid/ask level to exactly `[price, amount]`; Kraken's raw ccxt levels carry a third (timestamp) element Binance's don't. **`bot/engines/liquidity.py`**'s `compute_liquidity_snapshot()` computes spread/depth from that normalized shape. **`bot/engines/execution_comparison.py`**'s `compare_venues()` walks real order-book depth to estimate a specific order's all-in cost per venue — comparison data only, no routing decision, no arbitrage.
- **`ExecutionCoordinator`** (`bot/engines/execution_coordinator.py`) — holds one `ExecutionEngine` per venue, routes `execute_signal()` by an optional `signal["venue_id"]` (defaults to the coordinator's default venue). Each venue keeps its own `PositionSizer`/`DrawdownGuard` — confirmed independent (tripping one venue's drawdown guard doesn't affect another's).
- **`bot/engines/venue_readiness.py`**'s `assess_venue_readiness()` — real, evidence-based `NOT_READY`/`DRY_RUN_READY`/`PAPER_TRADING_READY`/`LIVE_READY` classification (dry-run order simulation + public connectivity + `validate_connection()`, all real calls). `PAPER_TRADING_READY` is currently unreachable for *either* venue: this codebase's "dry run" is always in-process fill simulation, never a real order routed to an exchange paper/testnet endpoint (Kraken has no spot testnet at all). Surfaced in `dry_run_test.py`'s section 10 on every run.
- **Backtester venue selection** (`Backtester`/`backtest(..., venue_id="binance")`) — swaps only the *cost model* applied to a backtest's fills, never the price data; `df` is always whatever the caller fetched (Binance-sourced in this project). A `venue_id="kraken"` backtest means "this Binance price history, Kraken's real fee schedule" — never a fabricated historical Kraken backtest.
- **Future-proofing note (IBKR/Saxo)**: `ExchangeAdapter`'s contract shape (symbol/quantity/price normalization, typed request/response, per-venue cost table) generalizes past crypto — but `BinanceAdapter`/`KrakenAdapter`'s constructor pattern (wrap a `ccxt` exchange object) is `ccxt`-specific and won't fit a non-`ccxt` broker client (IBKR's `ibapi`, Saxo's OAuth2 REST API) without a differently-shaped adapter internals; `OrderType` (`order_intent.py`) only has `MARKET`/`LIMIT` today, real brokers support many more (stop, stop-limit, trailing-stop) and should extend the enum only once a real integration needs a specific type, not speculatively; `get_execution_costs()`'s `fee_rate` is shaped as a fraction of notional (crypto-taker-fee-style) and doesn't fit a per-share/flat commission model without a shape change; and the execution stack's spot-only assumptions (`OrderManager`/`PositionSizer` reason in base-asset quantity, simple long/short via buy/sell) would need real rework for equities/options/futures, not just a new adapter.

### Two unrelated `Trade` classes — don't confuse them

- `Trade` (plain Python object, no DB) — the backtester's in-memory trade, defined in **`bot/journal/models.py`** (canonical) and duplicated verbatim in `bot/models.py` and `bot/core/trade.py`. The latter two are unused dead code (nothing imports them; `bot/core/trade.py` is only referenced by name in `dry_run_test.py`'s structural checklist). Always import `Trade` from `bot.journal.models` if you need it, or better, from wherever the local module already imports it (e.g. `bot.backtesting.backtester`).
- `TradeRecord` (`django.db.models.Model`, in `bot/journal/models.py`) — the persistent DB row for live trades, written by `TradeLogger`, read by dashboard/analytics views. Run `makemigrations`/`migrate` after changing its fields. Has a `venue` field (default `"binance"`); the unique-OPEN-trade constraint is scoped to `(symbol, venue)` together, not `symbol` alone — a single symbol can be OPEN on two different venues at once (see Multi-venue execution below), but not twice on the same venue.

### Regime detection & routing

`RegimeDetector.detect(df)` classifies every candle into `TRENDING_UP` / `TRENDING_DOWN` / `RANGING` / `HIGH_VOLATILITY` using ADX, ATR ratio, EMA spread, and Bollinger Band width (returned as a `RegimeResult` dataclass — every field always populated, so downstream code never needs `None` guards). `StrategyRouter` is purely a dispatcher: it decides *which* strategy fires for a regime, and never touches signal quality itself — that's each strategy's job.

### Backtesting (`bot/backtesting/`)

- `backtester.py` — `Backtester` class (and a `backtest(df, initial_balance=...)` function wrapper kept for backward compatibility) runs the full regime→router→strategy loop candle-by-candle, applies `bot/engines/simulation.py` (slippage/fees/PnL), and produces a result dict consumed by both the dashboard and `StrategyScorer`.
- `portfolio_backtester.py` — `PortfolioBacktester` runs `backtest()` across many symbols and ranks them with `StrategyScorer` (`bot/engines/strategy_scorer.py`, R-multiple-based, regime/structure/walk-forward aware) — used to find which coins actually suit the current strategy rather than tuning the strategy per-coin.
- `bot/engines/analytics_engine.py` (`TradeAnalytics`) aggregates trade lists into summary stats for both the dashboard and `bot/views/live_api.py`.

### Dashboard (`bot/views/dashboard.py`, `templates/dashboard.html`)

`dashboard()` fetches klines via `bot.data_fetcher.get_klines`, runs `backtest()`, caches results per-symbol in Django's LocMemCache for 1 hour, then normalizes trades into equity/drawdown curves for the template. Other endpoints: `portfolio_backtest_view`, `live_regime_monitor`, `walk_forward_view` (see `bot/urls.py`). `bot/views/live_api.py`'s `live_data` view is a stub — the trades list is hardcoded empty pending a real live data source.

### Data layer (`bot/data_fetcher.py`)

Standalone module (imported by dashboard, research pipeline, `fetch_all_symbols.py`) wrapping Binance REST directly with `requests`/`aiohttp` (not via `ccxt`, unlike the live bot runner which uses `ccxt`). Has its own disk-pickle cache (`bot/cache/*.pkl`, keyed by symbol+interval+candle-count, TTL-based) and hard safety ceilings: `MAX_PAGINATION_PAGES`, `MAX_RATE_LIMIT_RETRIES`, `MAX_ALLOWED_CANDLES = 50000` — all added specifically to prevent runaway loops from blocking the server thread. Provides both sync and async (`async_*`) variants of every fetch function.

### Research pipeline (`bot/research/`)

Offline, CLI-driven, not wired into Django. Rough flow: `fetch_all_symbols.py` → `data/*.csv` → `feature_calculator.py` / `cross_section_engine.py` → `research_data/*_cross_section.csv` → `build_observations.py` merges those into `research_data/observations.csv` → `feature_validator.py` / `feature_stability_analyzer.py` / `feature_decay_analyzer.py` validate and score features → `cointegration_engine.py` / `kalman_filter_engine.py` for pairs trading research → `walk_forward_engine.py` / `permutation_test_engine.py` for out-of-sample and statistical-significance checks. `run_research_all.py` orchestrates the feature calc + validation step across all symbols using `multiprocessing.Pool`.

### Health checks (`bot/management/commands/`)

`health_check` and `deep_health_check` are Django management commands, not pytest suites. `deep_health_check` explicitly does 5 layers: structural (imports/methods exist) → functional (runs without crashing) → behavioural (mathematically correct output) → integration (full data→regime→structure→signal→filter→sizer pipeline) → statistical drift (values still in historically sane ranges). When asked to verify the system still works end-to-end, run these rather than inventing new ad-hoc checks.

## Notable repo quirks

- Stray empty files at the project root (`0`, `0)`, `5`, `50`, `dict`, `float`, `str`, `tp`, `latest_ema50`, `prev_ema50`, `pd.DataFrame`) are leftover debug artifacts (likely from a shell redirection mistake), not part of the app — ignore them, don't treat them as meaningful config.
- `config/settings.py` has `DEBUG = True` and a hardcoded `SECRET_KEY` — this is a dev-only setup, not production-hardened.
- `bot_runner.py`'s `DRY_RUN` flag defaults to `True`; always confirm this is intentional before flipping it, and never flip it without the operator running `dry_run_test.py` first (per the file's own header instructions).
- Kraken support (env vars `KRAKEN_ENABLED`/`KRAKEN_DRY_RUN`/`KRAKEN_API_KEY`/`KRAKEN_API_SECRET`, read by `build_kraken_adapter()` in `bot/engines/kraken_adapter.py`) exists and is tested but is **not wired into `bot_runner.py`'s live loop** — setting these env vars alone does not make the live bot trade Kraken. See "Multi-venue execution" above.
