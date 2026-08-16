# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Django project ("Quant Bot Pro") that is really three things sharing one codebase:

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
python manage.py test bot                   # bot/tests.py is currently an empty stub — no real tests exist yet
python bot/core/dry_run_test.py             # run BEFORE ever starting the live bot — smoke-tests every component
python bot/core/bot_runner.py               # starts the live loop (defaults to DRY_RUN = True — see below)
python bot/fetch_all_symbols.py             # populates data/*.csv used by the research pipeline
python bot/run_research_all.py              # runs feature calc + validation across all symbols in data/
```

There's no formal test runner beyond Django's; `dry_run_test.py` and the two management health-check commands are the closest thing to an integration test suite in this repo.

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
- `OrderManager` — all exchange order placement; raises typed exceptions (`OrderStillOpenException`, `OrderUnconfirmedException`, `RateLimitException`) instead of returning `None`, so callers can react per-failure-mode.
- `PositionTracker` — in-memory dict of open positions (`{symbol: {...}}`), fast checks every candle; `is_reconciled` flag must be true (set by `reconcile_with_exchange()` on startup) before any trade is allowed — this is a hard safety gate in `execute_signal`.
- `MarketData` — live price/candle/balance fetching with its own cache.
- `PositionSizer` (`bot/risk/position_sizer.py`) — fixed-fractional sizing (default 1% risk/trade, hard-capped at 5%), floors quantity, never sizes up.
- `TradeLogger` (`bot/journal/trade_logger.py`) — writes `TradeRecord` (Django model) rows on entry/exit.

**Known broken import**: `bot/engines/market_data.py` does `from bot.config.execution_costs import CANDLE_CACHE_TTL`, but no `bot/config` package or `execution_costs.py` exists anywhere in the repo. Anything that instantiates `MarketData` (i.e. `ExecutionEngine`, hence the live `bot_runner.py` path) will currently raise `ImportError` until this module is created or the import is fixed.

### Two unrelated `Trade` classes — don't confuse them

- `Trade` (plain Python object, no DB) — the backtester's in-memory trade, defined in **`bot/journal/models.py`** (canonical) and duplicated verbatim in `bot/models.py` and `bot/core/trade.py`. The latter two are unused dead code (nothing imports them; `bot/core/trade.py` is only referenced by name in `dry_run_test.py`'s structural checklist). Always import `Trade` from `bot.journal.models` if you need it, or better, from wherever the local module already imports it (e.g. `bot.backtesting.backtester`).
- `TradeRecord` (`django.db.models.Model`, in `bot/journal/models.py`) — the persistent DB row for live trades, written by `TradeLogger`, read by dashboard/analytics views. Run `makemigrations`/`migrate` after changing its fields.

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
