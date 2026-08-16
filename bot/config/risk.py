# ============================================================
# bot/config/risk.py
# claude code changed: new file — fixes architecture audit finding M3.
# risk_pct=0.01 (1% risk per trade) was independently defined in three
# places that happened to agree by coincidence, not by any shared
# source: PositionSizer.__init__'s own default, ExecutionEngine's
# explicit PositionSizer(risk_pct=0.01) call, and backtester.py's
# module-level RISK_PER_TRADE constant. Nothing kept them in sync — a
# change to one silently would not propagate to the others. This file
# is now the single source; see position_sizer.py, execution_engine.py,
# and backtester.py for the call sites that were updated to use it.
# ============================================================

# Fraction of account balance risked per trade under fixed-fractional
# sizing. 0.01 = 1%.
DEFAULT_RISK_PCT = 0.01

# Hard safety ceiling — PositionSizer refuses to size above this even
# if risk_pct is misconfigured higher (e.g. 10 passed instead of 0.10).
MAX_RISK_PCT = 0.05

# claude code changed: new — portfolio-level drawdown kill switch
# threshold, see bot/risk/drawdown_guard.py. 0.15 = 15% drawdown from
# peak observed balance blocks NEW entries (existing open positions
# still close normally via stop/take-profit). At 1% risk per trade
# (DEFAULT_RISK_PCT above), 15% is roughly 15 consecutive full-stop
# losses with zero wins in between — a level that should never occur
# under a working strategy, so tripping it means something is
# structurally wrong (bad data, broken sizing, a strategy edge that's
# stopped working), not ordinary variance.
MAX_DRAWDOWN_PCT = 0.15

# claude code changed: new — fixes architecture audit finding C-2 (issue 2).
# DRY_RUN previously could never write a TradeRecord because bot_runner.py
# skipped execute_signal() entirely, AND even if it hadn't, MarketData.
# get_balance() calls the authenticated exchange.fetch_balance() endpoint,
# which fails without real Binance API keys. This is the simulated account
# balance MarketData.get_balance() returns instead when dry_run=True — lets
# the full sizing -> journaling pipeline run against paper money without
# requiring live credentials or touching the real exchange.
DRY_RUN_PAPER_BALANCE = 10000.0
